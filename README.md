# ByProduct

**One business's leftovers are another business's raw material.**

Small Nairobi businesses that make products from other businesses' leftovers can't easily get enough of them when they need them. Briquette makers need sawdust, upcyclers need fabric offcuts and insect farms need food waste. The material exists, but it sits in small piles across many workshops and market stalls, and nothing shows a buyer who has what, how far away they are, or how much is still free.

ByProduct is a Django REST API for a React app. Businesses list leftovers and post what they need. For each need, ByProduct finds the nearest sellers of that exact material, suggests how much to ask each one for until the need is covered, and tracks every pickup until the buyer confirms it was collected. Only collected kilos count as **kilos reused**.

## Tech stack

- Python 3.13, Django 6.1.2, Django REST Framework 3.18.3
- Logins: JWT with `djangorestframework-simplejwt` 5.5.1 (access and refresh tokens, refresh tokens rotate and are blacklisted on logout)
- `django-cors-headers` 4.9.0, so the React app on another address may call the API
- `drf-spectacular` 0.30.0 for the interactive docs at `/api/docs/`
- PostgreSQL on Neon (psycopg 3), configured with `dj-database-url`; settings read from `.env` with `python-dotenv`

## Run it locally (Windows, PowerShell)

```powershell
py -3.13 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill it in:

| Variable | Example | Purpose |
|---|---|---|
| `SECRET_KEY` | a long random string | Django signs logins and tokens with it (required) |
| `DEBUG` | `True` | `True` only on your own laptop |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Addresses the API answers on |
| `DATABASE_URL` | `postgresql://user:password@host/dbname?sslmode=require` | Neon connection string. Without it, a local SQLite file is used |
| `DEMO_PASSWORD` | anything you'll remember | The password of every demo login made by `seed_demo` |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated sites allowed to call the API from a browser (the React app) |

Then set up the database and start the server:

```powershell
python manage.py migrate
python manage.py load_basics      # 20 Nairobi areas and 8 materials; safe to run again
python manage.py seed_demo        # 12 demo businesses with logins, 9 live listings, 1,215 kg already reused
python manage.py runserver
```

Open **http://127.0.0.1:8000/api/docs/** to see and try every endpoint. To try protected ones there, log in with `POST /api/auth/login/`, click **Authorize** and paste the `access` token.

Demo logins (password = your `DEMO_PASSWORD`): `moto` is the briquette maker who needs sawdust; `doors`, `kamukunji`, `joinery`, `bedmakers` and `sofas` are the carpenters within 10 km; `timber` is 14.5 km away. Also `tailors`, `fruit`, `poultry`, `pamba` and `bsf`.

## Endpoints

All under `/api/`, all with a trailing slash. **Token** means the request needs `Authorization: Bearer <access>`.

| Method | URL | Auth | What it does |
|---|---|---|---|
| POST | `auth/register/` | public | Sign up: `username`, `password`, `name`, `kind`, `area`, `phone`. Returns tokens and the business |
| POST | `auth/login/` | public | `username`, `password` → `access`, `refresh`, `business` |
| POST | `auth/refresh/` | public | `refresh` → a new `access` and a new `refresh` (the old one stops working) |
| POST | `auth/logout/` | public | `refresh` → blacklists it |
| GET | `me/` | token | My business and dashboard: my listings, open needs, requests waiting for my answer, agreed pickups |
| PATCH | `me/` | token | Edit my business: `name`, `kind`, `area`, `phone` |
| DELETE | `me/` | token | Delete my account (refused if I have agreed or collected pickups) |
| GET | `areas/`, `areas/{id}/` | public | The 20 areas |
| GET | `materials/`, `materials/{id}/` | public | The 8 materials and their sack weights |
| GET | `businesses/`, `businesses/{id}/` | public | Public profiles: name, kind, area, completed pickups. Never a phone |
| GET | `listings/?material=&area=` | public | Live listings only, newest first |
| GET | `listings/{id}/` | public | One listing, live or not |
| POST | `listings/` | token | List leftovers: `material`, `quantity`, `unit`, `available_until`, optional `condition`, `price_per_unit`, `description` |
| PATCH | `listings/{id}/` | token, seller | Edit (can't shrink below kilos already promised; material fixed once anyone has asked) |
| DELETE | `listings/{id}/` | token, seller | Delete (refused if anything is agreed or collected: withdraw instead) |
| POST | `listings/{id}/withdraw/` | token, seller | Take it down; waiting requests are declined |
| POST | `listings/{id}/relist/` | token, seller | Copy a listing that's no longer live, for 7 more days |
| GET | `needs/` | token | My needs |
| POST | `needs/` | token | Post a need: `material`, `kg`, `max_km` (5, 10 or 20), `needed_by`, optional `note` |
| GET | `needs/{id}/` | token, buyer | The need, its progress and its pickup requests |
| PATCH | `needs/{id}/` | token, buyer | Edit `kg`, `max_km`, `needed_by`, `note` (not once closed; `kg` not below what's already asked for) |
| DELETE | `needs/{id}/` | token, buyer | Delete (refused if anything is agreed or collected: close instead) |
| GET | `needs/{id}/suggestions/` | token, buyer | Which sellers to ask and how much, nearest first |
| POST | `needs/{id}/ask-all/` | token, buyer | Ask every suggested seller in one go (all or nothing) |
| POST | `needs/{id}/close/` | token, buyer | Stop looking; waiting requests are cancelled |
| GET | `pickups/?status=` | token | Pickups where I'm the buyer or the seller |
| POST | `pickups/` | token, buyer | Ask one seller: `need`, `listing`, `kg` |
| GET | `pickups/{id}/` | token, buyer or seller | One pickup |
| PATCH | `pickups/{id}/` | token, buyer | Change `kg` while the seller hasn't answered |
| DELETE | `pickups/{id}/` | token, buyer | Take back a request the seller hasn't answered |
| POST | `pickups/{id}/accept/` | token, seller | Agree; both sides now see each other's phone |
| POST | `pickups/{id}/decline/` | token, seller | Say no |
| POST | `pickups/{id}/cancel/` | token, buyer or seller | Buyer cancels a waiting request; either side cancels an agreed one |
| POST | `pickups/{id}/collect/` | token, buyer | Confirm collection; closes the need when everything is in |
| GET | `impact/` | public | Kilos reused, completed pickups, kilos by material |
| GET | `docs/`, `schema/` | public | Interactive docs and the OpenAPI description |

### How responses look

- Requests send ids (`"material": 1`). Responses show related things with id and name (`"material": {"id": 1, "name": "Sawdust"}`).
- Lists are paginated, 20 per page: `{"count", "next", "previous", "results"}`.
- Create, read and edit return the thing itself. Action buttons return `{"message": "...", "pickup" | "listing" | "need": {...}}`, and ask-all returns `{"message", "pickups": [...]}`.
- Errors use DRF's defaults: `{"detail": "..."}` for 400 (a rule said no), 401 (no or expired token), 403 (wrong business) and 404 (not yours, or doesn't exist), and `{"field": ["message"]}` for a bad field.
- Phone numbers never appear on listings or public profiles. In a pickup, `other_phone` is the other side's number, and stays `null` until the seller agrees.

### One example per resource

These are real responses from the demo data.

**Log in** — `POST /api/auth/login/` with `{"username": "moto", "password": "..."}`
```json
{
  "refresh": "eyJhbGciOi...",
  "access": "eyJhbGciOi...",
  "business": {"id": 9, "name": "Moto Briquettes", "kind": "briquette maker",
               "area": {"id": 9, "name": "Kariobangi"}, "phone": "0722600100"}
}
```

**A listing** — `GET /api/listings/11/`
```json
{
  "id": 11,
  "seller": {"id": 2, "name": "Kamukunji Furniture Works", "area": {"id": 7, "name": "Kamukunji"}},
  "material": {"id": 1, "name": "Sawdust"},
  "quantity": 25, "unit": "sack", "kg": 300, "kg_left": 300,
  "condition": "sorted", "price_per_unit": 50, "available_until": "2026-11-17",
  "description": "Dry, no shavings. Collect after 4 pm.",
  "is_live": true, "created_at": "2026-11-10T23:51:34.381180+03:00"
}
```

**Post a need** — `POST /api/needs/` with `{"material": 1, "kg": 1200, "max_km": 10, "needed_by": "2026-11-13", "note": "Dry sawdust only"}` → 201
```json
{
  "id": 10,
  "business": {"id": 9, "name": "Moto Briquettes", "area": {"id": 9, "name": "Kariobangi"}},
  "material": {"id": 1, "name": "Sawdust"},
  "kg": 1200, "max_km": 10, "needed_by": "2026-11-13", "note": "Dry sawdust only", "closed": false,
  "progress": {"collected": 0, "agreed": 0, "waiting": 0, "still_needed": 1200},
  "created_at": "2026-11-10T23:51:41.121671+03:00"
}
```

**Suggestions** — `GET /api/needs/10/suggestions/` (first 2 of 5 rows)
```json
{
  "need": { "...the need, as above...": "" },
  "still_needed": 1200,
  "covered_kg": 1070,
  "suggestions": [
    {"listing": {"id": 10, "material": {"id": 1, "name": "Sawdust"}},
     "seller": {"id": 1, "name": "Kariobangi Doors and Frames", "area": {"id": 9, "name": "Kariobangi"}},
     "km": 0.0, "kg_left": 220, "suggested_kg": 220},
    {"listing": {"id": 13, "material": {"id": 1, "name": "Sawdust"}},
     "seller": {"id": 4, "name": "Gikomba Bed Makers", "area": {"id": 4, "name": "Gikomba"}},
     "km": 5.2, "kg_left": 250, "suggested_kg": 250}
  ]
}
```

**Ask a seller** — `POST /api/pickups/` with `{"need": 10, "listing": 10, "kg": 220}` → 201
```json
{
  "id": 10, "status": "pending", "status_label": "Waiting for the seller", "kg": 220,
  "listing": {"id": 10, "material": {"id": 1, "name": "Sawdust"}, "kg_left": 220},
  "buyer": {"id": 9, "name": "Moto Briquettes", "area": {"id": 9, "name": "Kariobangi"}},
  "seller": {"id": 1, "name": "Kariobangi Doors and Frames", "area": {"id": 9, "name": "Kariobangi"}},
  "other_phone": null,
  "created_at": "2026-11-10T23:51:41.590030+03:00", "updated_at": "2026-11-10T23:51:41.590500+03:00"
}
```

**An action** — the seller (logged in as `doors`) calls `POST /api/pickups/10/accept/`
```json
{
  "message": "Agreed. Call Moto Briquettes on 0722600100 to arrange the pickup.",
  "pickup": {"id": 10, "status": "accepted", "status_label": "Agreed", "kg": 220,
             "listing": {"id": 10, "material": {"id": 1, "name": "Sawdust"}, "kg_left": 0},
             "other_phone": "0722600100", "...": "buyer, seller and dates as above"}
}
```

**A public profile** — `GET /api/businesses/1/`
```json
{"id": 1, "name": "Kariobangi Doors and Frames", "kind": "carpenter",
 "area": {"id": 9, "name": "Kariobangi"}, "completed_pickups": 2}
```

**Impact** — `GET /api/impact/`
```json
{
  "total_kg": 1215, "pickups": 9,
  "by_material": [{"material": "Sawdust", "kg": 900, "bar_percent": 100},
                  {"material": "Market food waste", "kg": 280, "bar_percent": 31},
                  {"material": "Cotton offcuts", "kg": 35, "bar_percent": 4}]
}
```

**An error** — the buyer tries `POST /api/pickups/10/accept/` → 403
```json
{"detail": "Only the seller can accept."}
```

## How the React app logs in

1. **Log in or register** with `POST /api/auth/login/` (or `auth/register/`) and get `access` + `refresh`.
2. **Keep `access` in React state** (memory) and **`refresh` in `localStorage`**, so a page reload can log back in.
3. **Send `Authorization: Bearer <access>` on every request**, from one axios instance whose `baseURL` is `VITE_API_URL`:
   ```js
   const api = axios.create({ baseURL: import.meta.env.VITE_API_URL });   // e.g. http://127.0.0.1:8000/api
   api.interceptors.request.use((config) => {
     if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
     return config;
   });
   ```
4. **When the access token expires** (after 30 minutes) the API answers 401 with `"code": "token_not_valid"`. Call `POST /api/auth/refresh/` with the refresh token **once**, save **both** new tokens (the old refresh token no longer works), and retry the request. If the refresh fails too, log out.
5. **To log out**, `POST /api/auth/logout/` with `{"refresh": "<refresh>"}`, then clear React state and `localStorage`.

On page load, call `GET /api/me/` to know who's logged in. The React app's address must be in `CORS_ALLOWED_ORIGINS` (`http://localhost:5173` for Vite by default).

## How the rules work

All the business rules are in [materials/services.py](materials/services.py). Views only find the logged-in business, call one service function and turn its answer into JSON. When a rule says no, it raises `RuleError` (answered with 400) or `NotAllowed` when the wrong business is acting (403).

- **Kilos left** = the listing's kg minus agreed and collected pickups. It's calculated, never stored. Waiting requests don't hold kilos, so a seller who never answers can't freeze a pile.
- **Matching:** same material, still live, not your own, within your distance (the haversine formula between area centres). Nearest first, then biggest.
- **Suggested amounts:** go down the matches and ask each for the smaller of their kilos left and what you still need.
- **Accepting is safe:** the pickup and listing rows are locked while accepting (`select_for_update`), and other waiting requests that no longer fit are declined automatically.
- **Each seller is suggested once per need,** so someone who said no isn't asked again.

## Tests

```powershell
python manage.py test materials
```

44 tests in about 10 seconds. They run on a local SQLite file with a fast password hash. To run them on Postgres instead, where the row locking in `accept` really happens:

```powershell
$env:TEST_ON_NEON="1"; python manage.py test materials; Remove-Item Env:TEST_ON_NEON
```

That takes several minutes, because Django builds a temporary test database on Neon.

## Credits

Area locations © OpenStreetMap contributors and GeoNames. Sack weights are rough estimates, not measurements; adjust them in the admin.
