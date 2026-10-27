# ByProduct

**One business's leftovers are another business's raw material.**

Small Nairobi businesses that make products from other businesses' leftovers can't easily get enough of them when they need them. Briquette makers need sawdust, upcyclers need fabric offcuts and insect farms need food waste. The material exists, but it sits in small piles across many workshops and market stalls, and nothing shows a buyer who has what, how far away they are, or how much is still free.

ByProduct is a Django REST API where businesses list leftovers and post what they need. For each need it finds the nearest sellers of that exact material, suggests how much to ask each one for until the need is covered, and tracks every pickup until the buyer confirms it was collected. Only collected kilos count as **kilos reused**.

## Tech stack

- Python 3.13, Django 6.1, Django REST Framework 3.18
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
python manage.py createsuperuser    # optional, for /admin/
python manage.py runserver
```

Open http://127.0.0.1:8000/api/listings/ to use the API in the browser. Log in at http://127.0.0.1:8000/api-auth/login/.

## Logging in

There is no token system. The API uses DRF's built-in authentication:

- **Postman or curl:** send the username and password with each request (Basic auth). In Postman: Authorization → Basic Auth.
- **Browser:** log in once at `/api-auth/login/`.

Anyone can read areas, materials, live listings and the impact summary. Everything else needs a logged-in business.

## Endpoints

All under `/api/`. Write requests accept JSON or form data.

| Method | URL | Who | What it does |
|---|---|---|---|
| GET | `areas/` | anyone | The 20 areas |
| GET | `materials/` | anyone | The 8 materials and their sack weights |
| GET | `impact/` | anyone | Kilos reused, completed pickups, kilos by material |
| POST | `register/` | anyone | Create a login and its business |
| GET, PATCH | `profile/` | business | Your business details |
| GET | `listings/?material=<id>&area=<id>` | anyone | Live listings with `kg_left` |
| POST | `listings/` | business | List leftovers (in `kg` or `sack`) |
| GET | `listings/mine/` | business | All your listings, including expired and withdrawn |
| GET, PATCH | `listings/<id>/` | anyone / owner | View / edit (the material can't change) |
| POST | `listings/<id>/withdraw/` | owner | Take it down; waiting requests are declined |
| POST | `listings/<id>/again/` | owner | Re-post an expired listing for 7 more days |
| GET, POST | `needs/` | business | Your needs / post a need |
| GET | `needs/<id>/` | owner | The need, its progress and its pickup requests |
| GET | `needs/<id>/matches/` | owner | Suggested sellers and amounts, nearest first |
| POST | `needs/<id>/ask/` | owner | Ask one seller: `listing`, `kg` |
| POST | `needs/<id>/ask-all/` | owner | Ask every suggested seller (all or nothing) |
| POST | `needs/<id>/distance/` | owner | Change `max_km` to 5, 10 or 20 |
| POST | `needs/<id>/close/` | owner | Stop looking; waiting requests are cancelled |
| GET | `pickups/?status=pending` | business | Pickups where you are the buyer or the seller |
| POST | `pickups/<id>/accept/` | seller | Agree; returns the buyer's phone |
| POST | `pickups/<id>/decline/` | seller | Say no |
| POST | `pickups/<id>/cancel/` | buyer, or either side once agreed | Cancel; the kilos are free again |
| POST | `pickups/<id>/collected/` | buyer | Confirm collection; closes the need when complete |

**Responses**
- `200` / `201` with JSON. Actions also return a `message` to show the user.
- `400` when input is invalid (field errors) or a rule says no: `{"detail": "Only 220 kg is left on that listing."}`
- `401` when you're not logged in.
- `404` for something that isn't yours, such as another business's need or pickup.

**Privacy:** phone numbers never appear on public listings. In a pickup, `other_phone` shows the other business's number only once the seller has agreed.

### Example: the demo flow with curl

```powershell
# A buyer posts a need for 1,200 kg of sawdust (material 1) within 10 km
curl.exe -u moto:Briquettes-2026 -d "material=1" -d "kg=1200" -d "max_km=10" -d "needed_by=2026-10-30" http://127.0.0.1:8000/api/needs/

# See the suggested sellers, then ask them all
curl.exe -u moto:Briquettes-2026 http://127.0.0.1:8000/api/needs/1/matches/
curl.exe -u moto:Briquettes-2026 -X POST http://127.0.0.1:8000/api/needs/1/ask-all/

# A seller accepts one of the requests
curl.exe -u doors:<password> -X POST http://127.0.0.1:8000/api/pickups/1/accept/
```

## How the rules work

All the business rules are in [materials/services.py](materials/services.py). The views only read the request, call one service function, and turn its answer (or its `RuleError`) into JSON.

- **Kilos left** = the listing's kg minus agreed and collected pickups. It's calculated, never stored. Waiting requests don't hold kilos, so a seller who never answers can't freeze a pile.
- **Matching:** same material, still live, not your own, within your distance (the haversine formula between area centres). Nearest first, then biggest.
- **Suggested amounts:** go down the matches and ask each for the smaller of their kilos left and what you still need.
- **Accepting is safe:** the pickup and listing rows are locked during accept, and other waiting requests that no longer fit are declined automatically.
- **Each seller is suggested once per need,** so someone who said no isn't asked again.

## Tests

```powershell
python manage.py test materials
```

24 tests:
- 12 real-world scenarios against the rules, including the worked example: a 1,200 kg sawdust need in Kariobangi finds 5 sellers covering 1,070 kg.
- 11 API tests covering logins, privacy, ownership, validation and status codes.
- 1 test that `load_basics` creates no duplicates.

On Neon the run takes several minutes, because Django creates a temporary test database there.

## Credits

Area locations © OpenStreetMap contributors and GeoNames. Sack weights are rough estimates, not measurements; adjust them in the admin.
