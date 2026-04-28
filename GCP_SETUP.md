# Google Cloud Setup — One-Time Steps

You need to do this once to get a `credentials.json` file. The MCP uses it to request OAuth tokens on your behalf.

**Time: ~10 minutes.**

---

## 1. Create a Google Cloud project

1. Go to https://console.cloud.google.com/
2. Top bar → project dropdown → **New Project**
3. Name: `Claude Cowork MCP` (or whatever you like)
4. Click **Create**, wait a few seconds, then make sure the new project is selected in the top bar

---

## 2. Enable the APIs

For each of the eight APIs below, go to the link, make sure your new project is selected, and click **Enable**:

1. Gmail API — https://console.cloud.google.com/apis/library/gmail.googleapis.com
2. Google Calendar API — https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
3. Google Drive API — https://console.cloud.google.com/apis/library/drive.googleapis.com
4. Google Sheets API — https://console.cloud.google.com/apis/library/sheets.googleapis.com
5. Google Docs API — https://console.cloud.google.com/apis/library/docs.googleapis.com
6. Google Tasks API — https://console.cloud.google.com/apis/library/tasks.googleapis.com
7. People API (Contacts) — https://console.cloud.google.com/apis/library/people.googleapis.com
8. Google Chat API — https://console.cloud.google.com/apis/library/chat.googleapis.com

> ⚠️ **Google Chat needs a second setup step beyond just enabling the API.**
> If you skip the section below, every Chat tool call will return:
> `Not found (404): Google Chat app not found. To create a Chat app, you must turn on the Chat API and configure the app in the Google Cloud console.`
> Even with user-OAuth (you're not building a bot, just sending as yourself), the API requires a "Chat App" registration to exist for your project. See **Step 2b** below before moving on.

---

## 2c. Maps API setup *(optional — unlocks 10 maps_* tools)*

Maps APIs use a static API key (separate from OAuth). Setup is independent — you can skip this section entirely if you don't want Maps features.

**Cost:** Google offers $200/month free credit per Cloud account, which covers typical personal use. Beyond that, ~$5/1000 calls for most APIs.

**Step 1 — Enable billing on your GCP project**
- https://console.cloud.google.com/billing
- Maps APIs require billing enabled even on free tier — Google won't issue keys without a card on file.

**Step 2 — Enable the 7 Maps APIs**
- Geocoding: https://console.cloud.google.com/apis/library/geocoding-backend.googleapis.com
- Places (New): https://console.cloud.google.com/apis/library/places.googleapis.com
- Directions: https://console.cloud.google.com/apis/library/directions-backend.googleapis.com
- Distance Matrix: https://console.cloud.google.com/apis/library/distance-matrix-backend.googleapis.com
- Time Zone: https://console.cloud.google.com/apis/library/timezone-backend.googleapis.com
- Address Validation: https://console.cloud.google.com/apis/library/addressvalidation.googleapis.com
- Static Maps: https://console.cloud.google.com/apis/library/static-maps-backend.googleapis.com

**Step 3 — Create an API key**
- https://console.cloud.google.com/apis/credentials → **Create Credentials → API key**
- Copy the key (starts with `AIza...`).

**Step 4 — Restrict the key (security best practice)**
Click the key in the credentials list:
- **Application restrictions**: None (we call from a local Python script, not from a browser)
- **API restrictions**: **Restrict key** → tick all 7 Maps APIs from Step 2
- Save.

**Step 5 — Add to your config.json**
```json
{ "google_maps_api_key": "AIzaSy..." }
```
Or export `GOOGLE_MAPS_API_KEY` in your shell. The config-file path is more reliable on macOS GUI Cowork.

**Step 6 — Verify**
Restart Cowork, then ask: *"check my maps api key"*. The MCP runs a one-call test (geocode of Google HQ) and reports back. If it fails, the response includes the precise reason (billing, key invalid, API not enabled, etc.).

---

## 2d. Route Optimization API *(optional — unlocks `workflow_route_optimize_advanced`)*

Google's Route Optimization API solves real Vehicle Routing Problems (VRP) with time windows, multiple vehicles, capacities, and skip penalties. Much more powerful than the heuristic `workflow_route_optimize_visits` — and much more expensive (~$0.05–$0.20 per shipment vs ~$0.005 for the heuristic).

Unlike the other Maps APIs, Route Optimization **requires OAuth 2.0** with the `cloud-platform` scope — API keys are not accepted. The MCP handles this automatically; you just need to re-consent once after upgrade.

Skip this section if you don't need VRP — the heuristic tools work without it.

**Cost:** ~$0.05–$0.20 per shipment depending on tier. A 10-stop, 2-vehicle problem ≈ $1–$2.

**Step 1 — Enable the Route Optimization API**
- https://console.cloud.google.com/apis/library/routeoptimization.googleapis.com
- Click **Enable**. Billing must already be on (set up in Section 2c Step 1).

**Step 2 — Re-consent to add the `cloud-platform` OAuth scope**
The MCP scope list now includes `https://www.googleapis.com/auth/cloud-platform`. Your existing `token.json` was issued without it, so:
- Delete `token.json` from the project folder (the OAuth refresh token).
- Restart Cowork. On the next call that needs auth, you'll be prompted to re-consent — pick the same Google account, accept the (now-larger) scope set, and you're done.
- All your other Workspace tools keep working through the same flow.

**Step 3 — Confirm `gcp_project_id` is set**
The MCP auto-detects this from `credentials.json`. If you ever see the error `no_gcp_project_id`, add this to `config.json`:
```json
{ "gcp_project_id": "your-gcp-project-id" }
```
You can find your project ID at the top of https://console.cloud.google.com/.

**Step 4 — Verify**
Call `workflow_route_optimize_advanced` with a tiny test (2 stops, 1 vehicle, `dry_run=true` first to confirm the request body, then `dry_run=false`). The MCP sets the `Authorization: Bearer <token>` header from your re-consented OAuth credentials.
- HTTP 403 `permission_denied` → API not enabled in the project (Step 1).
- HTTP 401 `auth_failed` → scope not picked up; delete `token.json` and re-consent again.

---

## 2b. Configure the Chat App registration *(required for any Chat tool to work)*

Once the Chat API is enabled, you have to register a Chat App. This is the most-stuck-on step in the whole setup. Here's the path that works:

1. **Open the Chat API config page directly:**
   https://console.cloud.google.com/apis/api/chat.googleapis.com/hangouts-chat
2. Click the **Configuration** tab (top of the page).
3. **App information** — fill in:
   - **App name**: `Workspace MCP` (or whatever — only you'll see it)
   - **Avatar URL**: `https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/integration_instructions/default/24px.svg` (any public HTTPS image; this is a Google-hosted Material Symbol that's safe to use)
   - **Description**: `Personal MCP for sending Chat messages from Cowork`
4. **Functionality** — check both:
   - ☑ Receive 1:1 messages
   - ☑ Join spaces and group conversations

   *(You don't actually need these features — they're checked to satisfy the API's "is this app configured?" gate.)*
5. **Connection settings** — this is the field that traps everyone:
   - Pick the **HTTP endpoint URL** radio
   - Put: `https://YOUR_DOMAIN.com/api/chat-webhook`
   - **Why this URL doesn't have to actually work:** Google validates the format at config time (must be HTTPS, well-formed) but doesn't verify reachability. The URL only gets hit when someone interacts with the app. Since we never enable interactive features that would generate events, the URL never gets called and nothing breaks for sending. If you don't have a domain, `https://example.com/chat` is a Google-blessed placeholder.
6. **Visibility** — choose **Available to specific people and groups in your domain** and add your own email. This restricts the (non-functional) app to just you, which is what you want.
7. Click **Save** at the bottom.
8. **Wait 5-10 minutes** for the config to propagate before testing.

After the wait, restart Cowork and run `chat_list_spaces` (in Cowork: *"list my chat spaces"*). You should see your DMs and rooms instead of the 404 error.

**Cross-domain DMs:** if you want to message someone in a different Google Workspace organization (e.g., a partner at another company), their org's admin must allow external Chat from your domain. The MCP can't override this — it's a Workspace-level policy. If blocked, the `chat_find_or_create_dm` tool returns a clear `403 PERMISSION_DENIED` with a hint about what to ask their admin.

---

## 3. Configure the OAuth consent screen

1. Go to https://console.cloud.google.com/apis/credentials/consent
2. **User Type**: External → **Create**
3. Fill in the required fields:
   - App name: `Claude Cowork MCP`
   - User support email: your email
   - Developer contact: your email
4. Click **Save and Continue**
5. **Scopes** page: skip (click Save and Continue)
6. **Test users** page: Add your own Google account (`alice@example.com`). Click **Save and Continue**
7. Back to dashboard

> **Why "test user"?** Because the app is in testing mode, only listed test users can authorize it. That's fine — it's for you only.

---

## 4. Create OAuth client credentials

1. Go to https://console.cloud.google.com/apis/credentials
2. **+ Create Credentials** → **OAuth client ID**
3. **Application type**: Desktop app
4. **Name**: `Claude Cowork MCP Desktop`
5. Click **Create**
6. On the confirmation dialog, click **Download JSON**
7. **Rename the downloaded file to `credentials.json`**
8. Move it into this project folder:
   ```
   /Users/finnnai/Claude/google_workspace_mcp/credentials.json
   ```

> ⚠️ **Treat this file like a password.** It's already in `.gitignore` — don't commit it or share it.

---

## 5. You're done

First time you run the MCP, it'll open a browser window, ask you to sign in, and save a `token.json` beside `credentials.json`. After that, it refreshes automatically.

Next step → `README.md` for install + Cowork config.
