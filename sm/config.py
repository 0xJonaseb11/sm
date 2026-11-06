import os
from pathlib import Path
from dotenv import load_dotenv

# load .env from project root
_root = Path(__file__).parent.parent
load_dotenv(_root / ".env")


def _require(name: str) -> str:
    val = os.getenv(name, "")
    if not val:
        raise EnvironmentError(
            f"missing required env var: {name}\n"
            f"copy .env.example to .env and fill it in"
        )
    return val


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default)


class Config:
    fomoscan_key: str = ""
    pump_key: str = ""
    nansen_key: str = ""

    # api roots
    fomoscan_rest: str = "https://api.fomoscan.sh"
    fomoscan_ws: str = "wss://stream.fomoscan.sh/v3"
    pump_rest: str = "https://api.pump.fun/v1"
    nansen_rest: str = "https://api.nansen.ai/api/v1"

    # local db path
    db_path: str = str(_root / "sm.db")

    # how many rows to show in live feed before scrolling
    feed_rows: int = 40

    @classmethod
    def load(cls) -> "Config":
        c = cls()
        c.fomoscan_key = _optional("FOMOSCAN_KEY")
        c.pump_key = _optional("PUMP_KEY")
        c.nansen_key = _optional("NANSEN_KEY")
        return c

    def has_fomoscan(self) -> bool:
        return bool(self.fomoscan_key)

    def has_pump(self) -> bool:
        return bool(self.pump_key)

    def has_nansen(self) -> bool:
        return bool(self.nansen_key)


cfg = Config.load()
