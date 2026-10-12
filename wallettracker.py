import os
import requests
from flask import Flask, jsonify, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
DEXSCREENER_ENDPOINT = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
PUMPFUN_ENDPOINT = "https://frontend-api.pump.fun/coins/{mint}"

received_payloads = []


def compact_number(number: float) -> str:
    """Ubah angka besar menjadi format ringkas, misal 1.2M."""
    suffixes = ["", "k", "M", "B"]
    idx = 0
    while abs(number) >= 1000 and idx < len(suffixes) - 1:
        number /= 1000.0
        idx += 1
    return f"{number:.1f}{suffixes[idx]}"


def fetch_dex_fdv(mint: str):
    """Ambil FDV dari DexScreener."""
    try:
        resp = requests.get(DEXSCREENER_ENDPOINT.format(mint=mint), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs")
        if pairs:
            fdv = pairs[0].get("fdv")
            if fdv is not None:
                return compact_number(fdv)
    except Exception as exc:
        print(f"[DexScreener] Gagal mengambil FDV untuk {mint}: {exc}")
    return 0


def fetch_pumpfun_mcap(mint: str):
    """Ambil market cap dari Pump.fun."""
    try:
        resp = requests.get(PUMPFUN_ENDPOINT.format(mint=mint), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return compact_number(data["usd_market_cap"])
    except Exception as exc:
        print(f"[Pump.fun] Gagal mengambil market cap untuk {mint}: {exc}")
        return 0


def shorten_wallet(wallet: str) -> str:
    """Buat link Solscan dengan alamat yang dipendekkan."""
    return f"[({wallet[:4]}...{wallet[-4:]})](https://solscan.io/account/{wallet})"


def build_links(mint: str) -> str:
    """Kumpulan link berguna untuk token."""
    return (
        f"[Photon](https://photon-sol.tinyastro.io/en/lp/{mint}) | "
        f"[Trojan](https://t.me/diomedes_trojanbot?start=r-unauth0rized403-{mint}) | "
        f"[Axiom](https://axiom.trade/t/{mint}/@elonmusk?chain=sol) | "
        f"[DexScreener](https://dexscreener.com/solana/{mint})"
    )


def build_embed(description: str, color: int) -> dict:
    """Buat payload embed Discord."""
    return {
        "content": None,
        "embeds": [{"description": description, "color": color}],
        "username": "Solana Wallet Monitor",
        "avatar_url": "https://i.imgur.com/WjWkIHW.png",
        "attachments": [],
    }


def send_to_discord(payload: dict) -> None:
    """Kirim payload ke Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        print("[Discord] DISCORD_WEBHOOK_URL belum diatur.")
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as exc:
        print(f"[Discord] Gagal mengirim notifikasi: {exc}")


def handle_swap(tx: dict) -> None:
    """Proses transaksi tipe SWAP."""
    description = tx.get("description", "")
    wallet = tx.get("feePayer", "")
    parts = description.split()
    if not parts:
        return

    last_word = parts[-1]
    wallet_link = shorten_wallet(wallet)
    parts[0] = wallet_link
    short_desc = " ".join(parts)

    if last_word == "SOL":
        # Sale
        mint = tx["tokenTransfers"][0]["mint"]
        mcap = fetch_dex_fdv(mint)
        if mcap:
            desc = (
                f"🔴 ** SALE**\n{short_desc}\n```{mint}```\n"
                f"**Market Cap: **{mcap}\n**Links:** {build_links(mint)}"
            )
            send_to_discord(build_embed(desc, 16066086))
    else:
        # Buy
        mint = tx["tokenTransfers"][1]["mint"]
        mcap = fetch_dex_fdv(mint)
        if mcap:
            desc = (
                f"🟢 ** BUY**\n{short_desc}\n```{mint}```\n"
                f"**Market Cap: **{mcap}\n**Links:** {build_links(mint)}"
            )
            send_to_discord(build_embed(desc, 5697089))


def is_pumpfun_tx(tx: dict) -> bool:
    """Cek apakah transaksi terkait program Pump.fun."""
    try:
        if tx["instructions"][3]["accounts"][-1] == PUMPFUN_PROGRAM_ID:
            return True
    except (KeyError, IndexError):
        pass
    try:
        if tx["instructions"][2]["programId"] == PUMPFUN_PROGRAM_ID:
            return True
    except (KeyError, IndexError):
        pass
    return False


def handle_transfer(tx: dict) -> None:
    """Proses transaksi tipe TRANSFER yang berhubungan dengan Pump.fun."""
    if not is_pumpfun_tx(tx):
        return

    transfers = tx.get("tokenTransfers")
    if not transfers or not transfers[0].get("mint"):
        return

    mint = transfers[0]["mint"]
    token_qty = round(transfers[0]["tokenAmount"], 3)
    wallet = tx.get("feePayer", "")
    wallet_link = shorten_wallet(wallet)

    description = tx.get("description", "")
    words = description.split()
    first_word = words[0] if words else ""
    token_name = words[3] if len(words) > 3 else "Unknown"

    mcap = fetch_pumpfun_mcap(mint)
    sol_qty = round(abs(tx["accountData"][0]["nativeBalanceChange"]) / 1_000_000_000, 3)

    if first_word != wallet:
        # Buy
        desc = (
            f"💊🟢 ** BUY ON PUMP.FUN **\n"
            f"{wallet_link} swapped {sol_qty} SOL for {token_qty} {token_name} on Pump.fun\n"
            f"```{mint}```\n**Market Cap: **{mcap}\n"
            f"**Links:** {build_links(mint)} | [Pump.Fun](https://www.pump.fun/{mint})"
        )
        send_to_discord(build_embed(desc, 5697089))
    else:
        # Sale
        desc = (
            f"💊🔴 ** SALE ON PUMP.FUN **\n"
            f"{wallet_link} swapped {token_qty} {token_name} for {sol_qty} SOL on Pump.fun\n"
            f"```{mint}```\n**Market Cap: **{mcap}\n"
            f"**Links:** {build_links(mint)} | [Pump.Fun](https://www.pump.fun/{mint})"
        )
        send_to_discord(build_embed(desc, 16066086))


def handle_unknown(tx: dict) -> None:
    """Proses transaksi tipe UNKNOWN (misal dari Photon)."""
    transfers = tx.get("tokenTransfers")
    if not transfers:
        return
    mint = transfers[0].get("mint")
    if not mint:
        return

    token_qty = round(transfers[0]["tokenAmount"], 3)
    wallet = tx.get("feePayer", "")
    wallet_link = shorten_wallet(wallet)
    sol_qty = abs(tx["accountData"][0]["nativeBalanceChange"]) / 1_000_000_000

    # Ambil market cap, bisa dari Pump.fun atau DexScreener
    mcap = fetch_pumpfun_mcap(mint) or fetch_dex_fdv(mint) or "N/A"

    desc = (
        f"💊🟢 ** BUY ON PUMP.FUN (photon)**\n"
        f"{wallet_link} swapped {sol_qty} SOL for {token_qty} tokens on Pump.fun\n"
        f"```{mint}```\n**Market Cap: **{mcap}\n"
        f"**Links:** {build_links(mint)} | [Pump.Fun](https://www.pump.fun/{mint})"
    )
    send_to_discord(build_embed(desc, 5697089))


@app.route("/helius-webhook", methods=["GET", "POST"])
def helius_webhook():
    if request.method == "POST":
        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({"error": "Payload kosong"}), 400

        received_payloads.append(payload)

        if isinstance(payload, list) and payload:
            tx = payload[0]
            tx_type = tx.get("type")
            if tx_type == "SWAP":
                handle_swap(tx)
            elif tx_type == "TRANSFER":
                handle_transfer(tx)
            elif tx_type == "UNKNOWN":
                handle_unknown(tx)

        return jsonify({"message": "Webhook diterima"}), 200

    return jsonify({"history": received_payloads})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
