from fees import fomo_solana_fee, round_trip_fee


def test_bands_and_top_discount():
    assert fomo_solana_fee(4) == 0.10
    assert abs(fomo_solana_fee(20) - 0.40) < 1e-9  # 2%
    assert fomo_solana_fee(47.50) == 0.95
    assert fomo_solana_fee(100) == 0.95
    assert abs(fomo_solana_fee(200, 0) - 1.0) < 1e-9  # 0.50%
    assert abs(fomo_solana_fee(200, 0.10) - 0.90) < 1e-9  # 0.45%
    assert abs(round_trip_fee(200, 200, 0.0) - 2.0) < 1e-9


def test_default_discount_is_zero_on_flat_bands():
    assert fomo_solana_fee(50, 0.10) == 0.95
