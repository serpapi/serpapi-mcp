# OAuth 2.0 authentication — design proposal

> **Status**: design proposal, not an implementation.
> **Tracks**: [#38](https://github.com/serpapi/serpapi-mcp/issues/38) (Add OAuth 2.0 authentication support to the SerpApi MCP server).
> **Scope**: end-to-end design doc with a concrete user-flow, IdP decision matrix, and PR boundaries. Ready to be sliced into focused implementation PRs once the maintainer picks an IdP strategy.

## Context

The SerpApi MCP server currently authenticates with two methods:

- **Path-based**: `/YOUR_API_KEY/mcp`
- **Header-based**: `Authorization: Bearer YOUR_API_KEY`

Both work for direct integrations, but Anthropic's [official connector directory](https://claude.com/docs/connectors/building/submission#submission-requirements) requires **OAuth 2.0** to list a server as an official connector. Without OAuth, users can still use SerpApi as a **custom connector** but miss the one-click "Connect SerpApi" UX.

This doc lays out a concrete design for adding OAuth 2.0 to the server in a way that:

1. **Satisfies the Anthropic connector submission requirements**.
2. **Preserves backward compatibility** with the existing API-key path/header auth (so self-hosted users aren't broken).
3. **Doesn't require a SerpApi OAuth server to be built from scratch** — uses the upstream IdP's OAuth server (Google, GitHub, etc.) and maps the resulting identity to a SerpApi API key via a one-time provisioning flow.

## The MCP 2025-11-25 spec

The MCP spec standardized OAuth 2.0 as a transport-level auth mechanism
in [revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25).
Concretely:

- Hosts discover auth via `/.well-known/oauth-authorization-server`
  on the MCP server URL.
- The MCP server acts as an OAuth **proxy** (not a full identity
  provider). It forwards the authorization-code flow to an upstream
  IdP (Google, GitHub, etc.) and validates the resulting ID/access
  tokens.
- Tokens are validated by signature (JWKS) and audience/scope. The
  MCP server's own `search` tool calls remain authenticated via
  the original `SerpApi-API-Key` path, so the upstream SerpApi HTTP
  API is untouched.

`fastmcp>=3.2.0` (already a dependency) ships a complete
implementation of this spec in `fastmcp.server.auth`:

- `OAuthProxy` — generic OIDC-compliant proxy
- `JWTVerifier` — direct JWT validation (for custom IdPs)
- 16+ built-in provider integrations in
  `fastmcp.server.auth.providers` (Google, GitHub, Auth0, AWS
  Cognito, Azure AD, Clerk, WorkOS, Keycloak, Supabase, Discord,
  Descope, OCI, PropelAuth, Scalekit, in-memory for tests, debug)

The minimum viable integration is **~4 lines of config**:

```python
from fastmcp.server.auth.providers.google import GoogleProvider

auth = GoogleProvider(
    client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
)
mcp = FastMCP("SerpApi MCP Server", auth=auth)
```

The proxy handles the authorization-code flow, PKCE, token refresh,
JWKS-based signature verification, and scope checks. The only
SerpApi-specific work is mapping the IdP's identity claim to a
SerpApi API key (see `sub`→API key section below).

## IdP decision matrix

The maintainer needs to pick an IdP strategy. The matrix below
covers the realistic options:

| IdP | Effort | Pros | Cons |
|---|---|---|---|
| **Google Workspace** (org-level) | Lowest | Anthropic's primary use case is Claude for Work, which is Google SSO-flavored. Most users already have Google accounts. | Doesn't help users without Google accounts. Brand association may not fit all deployments. |
| **GitHub** (dev-facing) | Low | High overlap with SerpApi's developer audience. Easy OAuth app registration. | Personal repos only on free tier; org-level access needs GitHub Enterprise. |
| **Auth0** (generic OIDC) | Low–Med | Industry standard. Supports enterprise SSO (SAML, Okta, AD) out of the box. Free tier up to 7,000 MAU. | Adds a vendor dependency. |
| **WorkOS** (enterprise) | Med | Built for B2B SSO. If SerpApi's roadmap is enterprise customers, this is the natural fit. | Overkill for free-tier users. |
| **Custom OIDC** (e.g. SerpApi's own future auth) | Med–High | Maximum control. Future-proofs against IdP changes. | Requires building and maintaining an OAuth server. |

**Recommendation for v1**: start with **Google + GitHub in
parallel**, both behind the same `OAuthProxy` instance. Both providers
are first-class in `fastmcp.server.auth.providers`, and dual-IdP
covers 90%+ of the developer audience. Enterprise SSO (Auth0/WorkOS)
can be a v2 follow-up.

## Scope design

OAuth scopes should map to MCP capabilities:

| Scope | Maps to | Justification |
|---|---|---|
| `search:read` | `search` tool (any `mode`) | Read-side capability. Default for all authenticated users. |
| `search:execute` | `search` tool with `mode=complete` (default) | Same as read; explicit for clarity. |
| `search:bulk` | reserved | For future batch operations; not in current code. |

A single scope (`search:read`) is sufficient for the current
implementation. The matrix above is for future-proofing.

## `sub`→API key mapping flow

The SerpApi HTTP API itself does **not** support OAuth — it expects
an `api_key` query param. The MCP server needs to bridge the two
worlds. The proposed flow:

```
                          ┌──────────────────┐
                          │  IdP (Google)    │
                          │  issues          │
                          │  access_token + │
                          │  id_token        │
                          └────────┬─────────┘
                                   │ OIDC redirect
                                   ▼
┌─────────────────────────────────────────────────────┐
│  SerpApi MCP server (OAuth proxy)                   │
│  ─────────────────────────────────────────────────  │
│  1. Validate id_token (signature, aud, exp)         │
│  2. Extract sub claim (unique user ID)              │
│  3. Look up sub → SerpApi api_key in provisioning DB│
│  4. If no mapping exists:                           │
│     a. Generate new SerpApi api_key via SerpApi      │
│        account API (or require user to paste)       │
│     b. Persist sub → api_key mapping                │
│  5. Forward the api_key to the search() tool        │
│     via request.state (same as ApiKeyMiddleware)    │
└─────────────────────────┬───────────────────────────┘
                          │ api_key query param
                          ▼
                ┌──────────────────┐
                │  SerpApi HTTP    │
                │  search API      │
                └──────────────────┘
```

The provisioning DB can be a simple SQLite file
(`oauth_provisioning.sqlite3`) with schema:

```sql
CREATE TABLE oauth_mappings (
    idp_provider TEXT NOT NULL,        -- "google" | "github" | ...
    idp_sub      TEXT NOT NULL,        -- unique user ID from IdP
    serpapi_key  TEXT NOT NULL,        -- the SerpApi API key
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    UNIQUE(idp_provider, idp_sub)
);
```

For v1, a simpler approach is acceptable: a per-server
`SERPAPI_DEFAULT_API_KEY` env var that's used for all OAuth-authenticated
requests, with a TODO for per-user provisioning. This unblocks
the Anthropic connector submission without requiring a DB.

## Backward compatibility

The OAuth layer **must not break existing self-hosted users** who
authenticate via path or header. Proposed layering:

```
request
   │
   ▼
┌─────────────────────────────────────┐
│  OAuth middleware (NEW)            │   ← tries OAuth first
│  if Authorization: Bearer <jwt>    │      (when client supports it)
│     validate jwt, set sub on state │
└────────────┬────────────────────────┘
             │ no valid OAuth header
             ▼
┌─────────────────────────────────────┐
│  ApiKeyMiddleware (EXISTING)       │   ← fallback for path/header auth
│  extract api_key from path/header  │
└────────────┬────────────────────────┘
             │
             ▼
        search() tool
```

Both auth methods set `request.state.api_key` for `search()` to
read. The OAuth layer just adds another way to populate that state
field. The current `ApiKeyMiddleware` is untouched.

OAuth can be **opt-in** via env var: `MCP_OAUTH_ENABLED=false`
(default in self-hosted, default `true` on `mcp.serpapi.com`).

## Implementation phases (PR boundaries)

A single big-bang OAuth PR would be unreviewable (~200–500 lines
across `server.py`, new `oauth.py`, new DB module, env-var config,
README updates, hosted deploy changes). The proposal slices it into
focused, reviewable PRs:

### Phase 1: discovery endpoints (this PR's scope as a doc + skeleton)

- Add `docs/oauth-design.md` (this file).
- Add `src/oauth_discovery.py` with the
  `/.well-known/oauth-authorization-server` handler returning the
  spec-compliant metadata. **No actual auth** — just the discovery
  endpoint that the Anthropic connector directory scrapes to verify
  the server advertises OAuth support.
- CI: add a unit test that fetches the discovery endpoint and
  validates the JSON shape against the MCP 2025-11-25 schema.

### Phase 2: OAuthProxy skeleton (opt-in, flag-gated)

- Add `src/oauth_proxy.py` with the `OAuthProxy` mount logic.
- Add `MCP_OAUTH_ENABLED` env var (default `false`).
- Add `MCP_OAUTH_GOOGLE_CLIENT_ID` and
  `MCP_OAUTH_GOOGLE_CLIENT_SECRET` env vars.
- Add a fixture-based integration test that mocks the Google
  tokeninfo endpoint and exercises the full authorization-code
  flow against an in-process test client.

### Phase 3: sub→API key provisioning

- Add the provisioning DB module (SQLite, schema above).
- Add the `SERPAPI_DEFAULT_API_KEY` fallback for v1 simplicity.
- Wire the OAuth middleware to populate `request.state.api_key`
  from the provisioning table.

### Phase 4: GitHub provider + dual-IdP support

- Add `GitHubProvider` alongside `GoogleProvider`.
- Update the `OAuthProxy` mount to support both.
- Update the discovery endpoint's `authorization_endpoint` to be
  IdP-agnostic (or expose both via `prompt=select_account` style
  routing).

### Phase 5: hosted deploy + README

- Update the `mcp.serpapi.com` AWS Copilot config to set
  `MCP_OAUTH_ENABLED=true` and inject the secrets.
- Update README with the OAuth setup instructions.
- Update the Anthropic connector submission with the live
  discovery endpoint URL.

## Open questions for the maintainer

1. **IdP choice**: Google only, GitHub only, or both from v1?
2. **Provisioning**: per-user SerpApi API key (requires SerpApi
   account API or user-supplied key), or shared
   `SERPAPI_DEFAULT_API_KEY` for v1?
3. **Token lifetime**: short-lived access tokens (1h) + refresh
   tokens (30d)? Or session tokens (30d no refresh)?
4. **Hosted-only or also self-hosted?**: Anthropic's connector
   directory only lists the hosted `mcp.serpapi.com`. Should
   self-hosted users also get OAuth, or is it hosted-only?
5. **Backward compat window**: should `ApiKeyMiddleware` remain
   available indefinitely, or is there a sunset date after which
   path/header auth is removed?

## References

- [MCP 2025-11-25 spec, OAuth section](https://modelcontextprotocol.io/specification/2025-11-25)
- [Anthropic connector submission requirements](https://claude.com/docs/connectors/building/submission#submission-requirements)
- [`fastmcp.server.auth` documentation](https://gofastmcp.com/servers/auth)
- Issue #38: [Add OAuth 2.0 authentication support to the SerpApi MCP server](https://github.com/serpapi/serpapi-mcp/issues/38)
- PR #47: `[MCP] Add resource annotations to engines_index` (touches same transport boundary)
- PR #48: `[MCP] Migrate static engine resources to ResourceTemplate` (the `auth` parameter on `ResourceTemplate` ties directly into OAuth scope checks)
