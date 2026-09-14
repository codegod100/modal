"""OAuth authorization server whose login step is Modal's device token flow.

Modal has no self-serve OAuth for third parties, but `modal token new` is an
RFC 8628-style device grant: `TokenFlowCreate` returns a modal.com URL, the user
approves in a browser, and `TokenFlowWait` yields that user's API token plus the
workspace it belongs to. Verified to work with `localhost_port=0`, so no local
callback server is needed and it can be driven from a container.

MCP clients only drive login automatically when the server implements the MCP
authorization spec, so this wraps that device flow in a standard OAuth 2.1
server. `InMemoryOAuthProvider` supplies registration, PKCE, codes and refresh;
only the "who is this user" step is replaced.

`authorize()` cannot block for a browser login, so it redirects to a local page
that sends the user to Modal and polls until the flow lands, then forwards to
the MCP client's redirect URI with a normal authorization code.

Whoever logs in *is* the Modal user: their token is what the tools run with, so
authentication and authorization are the same act and no allowlist is needed.
Set `allowed_workspaces` to narrow it further.
"""

import asyncio
import html
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

# How long a started-but-uncompleted browser login stays valid.
LOGIN_TTL_SECONDS = 600
# Each TokenFlowWait call is a long poll; this bounds one server-side wait.
WAIT_POLL_SECONDS = 15.0


@dataclass
class ModalCredentials:
    """A Modal API token obtained from a completed token flow."""

    token_id: str
    token_secret: str
    workspace: str

    def env(self) -> dict[str, str | None]:
        """Environment for running the Modal CLI as this user.

        MODAL_IS_REMOTE must be cleared: inside a container the Modal client
        treats it as authoritative and *silently ignores* token environment
        variables, which would run every caller's commands with the container's
        own workspace-wide identity. `_cli.py` enforces this too; it is the one
        mistake in this design that fails open.
        """
        return {
            "MODAL_IS_REMOTE": None,
            "MODAL_TOKEN_ID": self.token_id,
            "MODAL_TOKEN_SECRET": self.token_secret,
        }


@dataclass
class _PendingLogin:
    client: OAuthClientInformationFull
    params: AuthorizationParams
    token_flow_id: str
    wait_secret: str
    web_url: str
    created_at: float = field(default_factory=time.time)
    redirect_to: str | None = None
    error: str | None = None
    task: Any = None


class ModalTokenFlowProvider(InMemoryOAuthProvider):
    """OAuth provider that authenticates callers as Modal users."""

    def __init__(
        self,
        *,
        base_url: str,
        allowed_workspaces: list[str] | None = None,
        **kwargs,
    ):
        kwargs.setdefault(
            "client_registration_options",
            # MCP clients discover this server and register themselves; without
            # dynamic registration there is no way for them to obtain a client_id.
            ClientRegistrationOptions(enabled=True),
        )
        super().__init__(base_url=base_url, **kwargs)
        self._allowed = {w.strip() for w in (allowed_workspaces or []) if w.strip()}
        self._logins: dict[str, _PendingLogin] = {}
        # Modal credentials, keyed by the access token we issued for them.
        self._credentials: dict[str, ModalCredentials] = {}

    # -- login plumbing ---------------------------------------------------

    async def _start_token_flow(self) -> tuple[str, str, str]:
        """Begin a Modal device login. Returns (flow_id, wait_secret, web_url)."""
        from modal.client import _Client
        from modal.config import config
        from modal_proto import api_pb2

        async with _Client.anonymous(config["server_url"]) as client:
            resp = await client.stub.TokenFlowCreate(
                api_pb2.TokenFlowCreateRequest(
                    utm_source="modal-mcp",
                    next_url="",
                    # No local callback server: this server is not on the user's
                    # machine. Verified that Modal completes the flow anyway.
                    localhost_port=0,
                )
            )
        return resp.token_flow_id, resp.wait_secret, resp.web_url

    async def _await_token_flow(self, login_id: str) -> None:
        """Poll Modal until the user approves, then mint an authorization code."""
        from modal.client import _Client
        from modal.config import config
        from modal_proto import api_pb2

        pending = self._logins.get(login_id)
        if pending is None:
            return
        deadline = time.time() + LOGIN_TTL_SECONDS
        try:
            async with _Client.anonymous(config["server_url"]) as client:
                while time.time() < deadline:
                    resp = await client.stub.TokenFlowWait(
                        api_pb2.TokenFlowWaitRequest(
                            token_flow_id=pending.token_flow_id,
                            wait_secret=pending.wait_secret,
                            timeout=WAIT_POLL_SECONDS,
                        ),
                        retry=None,
                        timeout=WAIT_POLL_SECONDS + 5,
                    )
                    if resp.timeout:
                        continue

                    workspace = resp.workspace_username
                    if self._allowed and workspace not in self._allowed:
                        pending.error = (
                            f"Workspace {workspace!r} is not permitted to use this server."
                        )
                        logger.warning("rejected login for workspace %r", workspace)
                        return

                    credentials = ModalCredentials(
                        token_id=resp.token_id,
                        token_secret=resp.token_secret,
                        workspace=workspace,
                    )
                    # Mint a normal OAuth code now that the user is known.
                    redirect = await super().authorize(pending.client, pending.params)
                    code = redirect.split("code=", 1)[1].split("&", 1)[0]
                    self._credentials[code] = credentials
                    pending.redirect_to = redirect
                    logger.info("login completed for workspace %r", workspace)
                    return
            pending.error = "Login timed out. Start again."
        except Exception as exc:  # surfaced to the browser, not swallowed
            logger.exception("token flow failed")
            pending.error = f"Login failed: {exc}"

    # -- OAuth overrides --------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if client.client_id not in self.clients:
            raise AuthorizeError(
                error="unauthorized_client",
                error_description=f"Client '{client.client_id}' not registered.",
            )
        self._expire_logins()
        flow_id, wait_secret, web_url = await self._start_token_flow()
        login_id = secrets.token_urlsafe(24)
        pending = _PendingLogin(
            client=client,
            params=params,
            token_flow_id=flow_id,
            wait_secret=wait_secret,
            web_url=web_url,
        )
        self._logins[login_id] = pending
        # Start waiting immediately so approval is caught even if the browser
        # is slow to load the polling page.
        pending.task = asyncio.create_task(self._await_token_flow(login_id))
        return f"{str(self.base_url).rstrip('/')}/modal/login?id={login_id}"

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code
    ) -> OAuthToken:
        credentials = self._credentials.pop(authorization_code.code, None)
        token = await super().exchange_authorization_code(client, authorization_code)
        if credentials is not None:
            # Re-key the credentials onto the access token the caller will present.
            self._credentials[token.access_token] = credentials
            if token.refresh_token:
                self._credentials[token.refresh_token] = credentials
        return token

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        credentials = self._credentials.get(
            getattr(refresh_token, "token", "") or ""
        )
        token = await super().exchange_refresh_token(client, refresh_token, scopes)
        if credentials is not None:
            self._credentials[token.access_token] = credentials
            if token.refresh_token:
                self._credentials[token.refresh_token] = credentials
        return token

    def credentials_for(self, token: str) -> ModalCredentials | None:
        """Modal credentials belonging to an issued access token."""
        return self._credentials.get(token)

    async def load_access_token(self, token: str):
        """Attach the caller's Modal identity to the verified token.

        Carrying the credentials in the token's claims means tools can reach
        them with `get_access_token()` alone, without a handle on this provider.
        """
        access = await super().load_access_token(token)
        if access is None:
            return None
        credentials = self._credentials.get(token)
        if credentials is None:
            # Authenticated but with no Modal identity bound: refuse rather than
            # fall through to whatever ambient identity the container has.
            logger.warning("access token has no Modal credentials bound")
            return None
        access.claims = {
            **(getattr(access, "claims", None) or {}),
            "workspace": credentials.workspace,
            "modal_token_id": credentials.token_id,
            "modal_token_secret": credentials.token_secret,
        }
        return access

    def _expire_logins(self) -> None:
        cutoff = time.time() - LOGIN_TTL_SECONDS
        for key in [k for k, v in self._logins.items() if v.created_at < cutoff]:
            self._logins.pop(key, None)

    # -- browser-facing routes -------------------------------------------

    async def _login_page(self, request: Request) -> HTMLResponse:
        login_id = request.query_params.get("id", "")
        pending = self._logins.get(login_id)
        if pending is None:
            return HTMLResponse("<h1>This login link has expired.</h1>", status_code=404)
        url = html.escape(pending.web_url)
        # Absolute: a relative path here resolves against /modal/ and silently
        # becomes /modal/modal/login/status, so the page polls a 404 forever and
        # the login never completes.
        status_url = f"{str(self.base_url).rstrip('/')}/modal/login/status"
        body = f"""<!doctype html>
<title>Sign in with Modal</title>
<style>
 body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 34rem; margin: 15vh auto;
        padding: 0 1.5rem; color: #111; }}
 a.button {{ display: inline-block; background: #111; color: #fff; padding: .7rem 1.1rem;
        border-radius: 6px; text-decoration: none; }}
 .muted {{ color: #666; font-size: .9rem; }}
 @media (prefers-color-scheme: dark) {{
   body {{ background: #111; color: #eee; }} a.button {{ background: #eee; color: #111; }}
   .muted {{ color: #999; }} }}
</style>
<h1>Sign in with Modal</h1>
<p>Approve this server in Modal, then come back here. This page finishes automatically.</p>
<p><a class="button" href="{url}" target="_blank" rel="noopener">Open Modal sign-in</a></p>
<p class="muted" id="status">Waiting for approval…</p>
<script>
 const id = {login_id!r};
 const statusUrl = {status_url!r};
 setTimeout(() => window.open({url!r}, "_blank", "noopener"), 300);
 async function poll() {{
   try {{
     const r = await fetch(statusUrl + "?id=" + encodeURIComponent(id));
     const d = await r.json();
     if (d.status === "done") {{ window.location = d.redirect; return; }}
     if (d.status === "error") {{
       document.getElementById("status").textContent = d.message; return;
     }}
   }} catch (e) {{ /* keep polling */ }}
   setTimeout(poll, 1500);
 }}
 poll();
</script>"""
        return HTMLResponse(body)

    async def _login_status(self, request: Request) -> JSONResponse:
        pending = self._logins.get(request.query_params.get("id", ""))
        if pending is None:
            return JSONResponse({"status": "error", "message": "Login expired."})
        if pending.error:
            return JSONResponse({"status": "error", "message": pending.error})
        if pending.redirect_to:
            return JSONResponse({"status": "done", "redirect": pending.redirect_to})
        return JSONResponse({"status": "pending"})

    async def _as_metadata(self, request: Request) -> JSONResponse:
        """Authorization server metadata.

        Overrides the stock document for two reasons. It must advertise the
        `none` auth method: MCP clients are public clients that cannot hold a
        secret, and a strict one reads this list, concludes it has no usable
        auth method, and gives up before it ever tries to register. It is also
        served at the RFC 8414 path-scoped location, which clients derive from
        the resource URL and try before the root.
        """
        base = str(self.base_url).rstrip("/")
        return JSONResponse(
            {
                "issuer": f"{base}/",
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "registration_endpoint": f"{base}/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": [
                    "none",
                    "client_secret_post",
                    "client_secret_basic",
                ],
                "code_challenge_methods_supported": ["S256"],
                "scopes_supported": [],
            }
        )

    async def _resource_metadata(self, request: Request) -> JSONResponse:
        """Unscoped protected-resource metadata.

        The spec-correct document is path-scoped (`.../oauth-protected-resource/mcp`)
        and is what the 401 challenge advertises, but some clients look for it at
        the root. Serving both costs nothing and avoids a dead end.
        """
        base = str(self.base_url).rstrip("/")
        return JSONResponse(
            {
                "resource": f"{base}/mcp",
                "authorization_servers": [f"{base}/"],
                "scopes_supported": [],
                "bearer_methods_supported": ["header"],
            }
        )

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        # Ours come first: Starlette matches in order, so these shadow the stock
        # metadata route rather than sitting unreachable behind it.
        return [
            Route("/modal/login", self._login_page, methods=["GET"]),
            Route("/modal/login/status", self._login_status, methods=["GET"]),
            Route(
                "/.well-known/oauth-authorization-server",
                self._as_metadata,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-authorization-server/mcp",
                self._as_metadata,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-protected-resource",
                self._resource_metadata,
                methods=["GET"],
            ),
            *super().get_routes(mcp_path),
        ]
