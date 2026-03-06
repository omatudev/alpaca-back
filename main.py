from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from auth import require_auth, verify_google_token, create_jwt, _allowed_emails
from config import settings
from database import init_db
from routers import portfolio, orders, watchlist
from routers import alpaca as alpaca_router
from streaming.router import router as ws_router, manager as ws_manager
from streaming.price_feed import PriceFeed

_price_feed = PriceFeed(ws_manager)


class GoogleAuthRequest(BaseModel):
    token: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup logic before yield, shutdown logic after."""
    await init_db()
    await _price_feed.start()
    yield
    await _price_feed.stop()


app = FastAPI(
    title="My Broker API",
    description="Personal trading application API",
    version="0.1.0",
    lifespan=lifespan,
)

@app.get("/")
def root():
    return {"status": "ok"}

# CORS — allow local frontend dev servers and any production origins from settings
default_origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]
extra = [o.strip() for o in settings.frontend_origins.split(",") if o.strip()]
allow_origins = default_origins + extra

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/auth/google")
async def google_auth(body: GoogleAuthRequest):
    """Exchange a Google id_token for a JWT."""
    payload = verify_google_token(body.token)
    email = payload.get("email", "")
    if email not in _allowed_emails():
        from fastapi import HTTPException, status as http_status
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Access denied — email not authorized",
        )
    jwt_token = create_jwt(email)
    return {
        "token": jwt_token,
        "email": email,
        "name": payload.get("name", ""),
        "picture": payload.get("picture", ""),
    }


# Protected routers
_auth = [Depends(require_auth)]
app.include_router(portfolio.router,      prefix="/api/portfolio", tags=["portfolio"], dependencies=_auth)
app.include_router(orders.router,         prefix="/api/orders",    tags=["orders"],    dependencies=_auth)
app.include_router(watchlist.router,      prefix="/api/watchlist", tags=["watchlist"], dependencies=_auth)
app.include_router(alpaca_router.router,  prefix="/api/alpaca",    tags=["alpaca"],    dependencies=_auth)
app.include_router(ws_router, tags=["streaming"])  # WS auth handled inside the endpoint
