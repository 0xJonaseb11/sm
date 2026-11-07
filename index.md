# Market overview

```sh
python sm.py trends
python sm.py board fomo
python sm.py board pump
```

# Trader deep dive

```sh
python sm.py trader frankdegods
python sm.py trader frankdegods --pnl
```

# dash scan scripts

```sh
# all 4 pnl windows side by side + drill top 5 traders
python scripts/fomo.py --drill 5

# pump leaderboard + trending tokens side by side
python scripts/pump.py --top 15

# nansen dex trades for a token (replace with any solana token addr)
python scripts/nansen.py 5THtNEGTF1y67eRWvcgz3MomDZePCVx1xdskHHeCpump

# enrich top 25 pump traders with full identity (fomoscan + nansen)
python scripts/enrich.py --board pump --top 25
```

# Hyperliquid signals

```sh
# index top 200 HL traders and compute smart money bias per asset
python scripts/signals.py --hl-top 200

# who is long/short on specific assets
python scripts/signals.py --token HYPE
python scripts/signals.py --token BTC
python scripts/signals.py --token ETH

# open positions for a specific wallet
python scripts/signals.py --wallet 0xf5d81a135f756ca16544e53c20fc20643ec3ad53
```

# Live watchers

```sh
# watch top 10 fomo traders for new callouts (ctrl+c to stop)
python scripts/wtc.py --board fomo --top 10

# poll for position opens/closes/flips every 2 min
python scripts/signals.py --watch --interval 120
```

# SM rest api

```sh
python sm.py api --port 8787
# then hit: http://localhost:8787/signals
#           http://localhost:8787/signals/HYPE
#           http://localhost:8787/traders
#           http://localhost:8787/positions/live
```