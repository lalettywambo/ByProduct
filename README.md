# ByProduct

**One business's leftovers are another business's raw material.**

Small Nairobi businesses that make products from other businesses' leftovers can't easily get enough of them when they need them. Briquette makers need sawdust, upcyclers need fabric offcuts and insect farms need food waste. The material exists, but it sits in small piles across many workshops and market stalls, and nothing shows a buyer who has what, how far away they are, or how much is still free.

ByProduct is a Django REST API where businesses list leftovers and post what they need. For each need it finds the nearest sellers of that exact material, suggests how much to ask each one for until the need is covered, and tracks every pickup until the buyer confirms it was collected. Only collected kilos count as **kilos reused**.

## Tech stack

- Python 3.13, Django 6.1.2, Django REST Framework 3.18.3
- PostgreSQL on Neon (psycopg 3), configured with `dj-database-url`
- Settings read from `.env` with `python-dotenv`

## Run it locally (Windows, PowerShell)

```powershell
py -3.13 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project folder:

| Variable | Example | Purpose |
|---|---|---|
| `SECRET_KEY` | a long random string | Django's signing key (required) |
| `DEBUG` | `True` | `True` only on your own laptop |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Hosts the site answers on |
| `DATABASE_URL` | `postgresql://user:password@host/dbname?sslmode=require` | Neon connection string. Without it, SQLite is used. |

Then set up the database and start the server:

```powershell
python manage.py migrate
python manage.py load_basics        # 20 Nairobi areas and 8 materials; safe to run again
python manage.py runserver
```

Open http://127.0.0.1:8000/api/basics/ to try the API in the browser.

## No login (demo version)

This is a capstone demo, so there are no accounts. Every request that acts says which business is acting:
- a `"business": <id>` field in the body for POST requests
- `?business=<id>` in the URL for `GET /api/needs/<id>/`

The rules still check that it's the right business. For example, only the seller can accept, and only the buyer can mark a pickup as collected.

## Endpoints

All under `/api/`. Request bodies can be JSON or form data.

| Method | URL | What it does |
|---|---|---|
| GET | `basics/` | Areas, materials and businesses, for dropdowns and the "I am" picker |
| POST | `businesses/` | Add a business: `name`, `kind`, `area`, `phone` |
| GET | `businesses/<id>/` | Dashboard: its listings, open needs, requests waiting for its answer, and agreed pickups with the other side's phone |
| GET | `listings/?material=<id>&area=<id>` | Browse live listings with `kg_left` |
| POST | `listings/` | List leftovers: `business`, `material`, `quantity`, `unit` (`kg` or `sack`), `available_until`, plus optional `condition`, `price_per_unit`, `description` |
| POST | `listings/<id>/withdraw/` | Take a listing down; waiting requests are declined |
| POST | `listings/<id>/again/` | Re-post a listing that's no longer live, for 7 more days |
| POST | `needs/` | Post a need: `business`, `material`, `kg`, `max_km` (5, 10 or 20), `needed_by`, optional `note` |
| GET | `needs/<id>/?business=<id>` | Progress, suggested sellers and pickup requests in one response (buyer only) |
| POST | `needs/<id>/ask/` | Ask one seller: `business`, `listing`, `kg` |
| POST | `needs/<id>/ask-all/` | Ask every suggested seller (all or nothing) |
| POST | `needs/<id>/distance/` | Change `max_km` to 5, 10 or 20 |
| POST | `needs/<id>/close/` | Stop looking; waiting requests are cancelled |
| POST | `pickups/<id>/accept/` | Seller agrees (the response includes the buyer's phone) |
| POST | `pickups/<id>/decline/` | Seller says no |
| POST | `pickups/<id>/cancel/` | Buyer cancels a waiting request, or either side cancels an agreed pickup |
| POST | `pickups/<id>/collected/` | Buyer confirms collection; closes the need when everything is in |
| GET | `impact/` | Kilos reused, completed pickups and kilos by material |

**Responses**
- `200` / `201` with JSON. Actions also return a `message` to show the user.
- `400` when input is invalid or a rule says no: `{"detail": "Only the seller can accept."}`
- `400` with `{"business": "Say which business is acting."}` when the acting business is missing.
- `404` for an unknown id or action, or a need opened by a business that didn't post it.

**Privacy:** phone numbers never appear on listings. In a pickup, `other_phone` shows the other side's number only once the pickup is agreed or collected.

### Example: the demo flow with curl

```powershell
# Moto Briquettes (business 9) needs 1,200 kg of sawdust (material 1) within 10 km
curl.exe -d "business=9" -d "material=1" -d "kg=1200" -d "max_km=10" -d "needed_by=2026-10-30" http://127.0.0.1:8000/api/needs/

# See the suggested sellers, then ask them all
curl.exe "http://127.0.0.1:8000/api/needs/1/?business=9"
curl.exe -d "business=9" http://127.0.0.1:8000/api/needs/1/ask-all/

# Kariobangi Doors (business 1) accepts its request
curl.exe -d "business=1" http://127.0.0.1:8000/api/pickups/1/accept/
```

## How the rules work

All the business rules are in [materials/services.py](materials/services.py). The views only find the acting business, call one service function, and turn its answer (or its `RuleError`) into JSON.

- **Kilos left** = the listing's kg minus agreed and collected pickups. It's calculated, never stored. Waiting requests don't hold kilos, so a seller who never answers can't freeze a pile.
- **Matching:** same material, still live, not your own, within your distance (the haversine formula between area centres). Nearest first, then biggest.
- **Suggested amounts:** go down the matches and ask each for the smaller of their kilos left and what you still need.
- **Accepting is safe:** the pickup and listing rows are locked during accept, and other waiting requests that no longer fit are declined automatically.
- **Each seller is suggested once per need,** so someone who said no isn't asked again.

## Tests

```powershell
python manage.py test materials
```

18 tests:
- 12 real-world scenarios against the rules, including the worked example: a 1,200 kg sawdust need in Kariobangi finds 5 sellers covering 1,070 kg.
- 5 API tests: the worked example end to end, a missing acting business, the wrong business accepting or collecting, phone privacy, and impact.
- 1 test that `load_basics` creates no duplicates.

On Neon the run takes several minutes, because Django creates a temporary test database there.

## Credits

Area locations © OpenStreetMap contributors and GeoNames. Sack weights are rough estimates, not measurements; adjust them in the admin.
