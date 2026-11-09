"""Acceptance test 1: links.py is exactly the STEP 6 builder."""

import logging

from links import CHAIN_ID, SIGNUP_URL, coin_url, profile_url, require_canonical


def test_chain_ids_and_referral():
    assert CHAIN_ID == {"solana": 1399811149, "robinhood": 4663, "base": 8453, "bnb": 56}
    assert SIGNUP_URL == "https://fomo.family/r/savipww"
    for chain, chain_id in CHAIN_ID.items():
        url = coin_url("Mint111", chain)
        assert url == (
            "https://fomo.family/coin?address=Mint111"
            f"&chainId={chain_id}&r=savipww&source=share_link"
        )
        assert "r=savipww" in url
        require_canonical(url)


def test_default_chain_is_solana():
    assert "chainId=1399811149" in coin_url("Abc")


def test_unknown_chain_returns_signup(caplog):
    with caplog.at_level(logging.WARNING):
        assert coin_url("Abc", "ethereum") == SIGNUP_URL
    assert "ethereum" in caplog.text
    require_canonical(SIGNUP_URL)


def test_profile_strips_at_and_keeps_referral():
    url = profile_url("@WillLarp")
    assert url == "https://fomo.family/profile/WillLarp?source=share_link&r=savipww"
    assert profile_url("WillLarp") == url


def test_links_are_not_shortened():
    for url in (coin_url("Abc"), profile_url("WillLarp"), SIGNUP_URL):
        assert "bit.ly" not in url
        assert "tinyurl" not in url
        assert url.startswith("https://fomo.family/")
