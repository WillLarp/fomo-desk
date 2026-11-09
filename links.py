"""The only FOMO URL builder (STEP 6).

Every fomo.family link in the desk comes from this module. Keep the
referral parameter on every URL. Do not shorten or redirect these links.
"""

import logging

log = logging.getLogger("desk.links")

CHAIN_ID = {"solana": 1399811149, "robinhood": 4663, "base": 8453, "bnb": 56}
SIGNUP_URL = "https://fomo.family/r/savipww"


def coin_url(address, chain="solana"):
    if chain not in CHAIN_ID:
        log.warning("unknown FOMO chain %s", chain)
        return SIGNUP_URL
    return (
        f"https://fomo.family/coin?address={address}"
        f"&chainId={CHAIN_ID[chain]}&r=savipww&source=share_link"
    )


def profile_url(handle):
    h = handle.lstrip("@")
    return f"https://fomo.family/profile/{h}?source=share_link&r=savipww"


def require_canonical(url):
    """Refuse a FOMO link that dropped the referral or was shortened."""
    if not isinstance(url, str) or not url.startswith("https://fomo.family/"):
        raise ValueError("FOMO link must come from links.py")
    if "r=savipww" not in url and "/r/savipww" not in url:
        raise ValueError("FOMO link is missing the referral")
    lowered = url.lower()
    if any(host in lowered for host in ("bit.ly", "tinyurl", "t.co/", "is.gd")):
        raise ValueError("FOMO link must not be shortened")
    return url
