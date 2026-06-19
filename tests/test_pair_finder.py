from screening.pair_finder import compute_correlation, compute_hedge_ratio, screen_pairs


def test_compute_correlation_perfectly_correlated():
    prices_a = [100 + i for i in range(50)]
    prices_b = [50 + i * 0.5 for i in range(50)]
    correlation = compute_correlation(prices_a, prices_b)
    assert correlation > 0.99


def test_compute_correlation_uncorrelated():
    import random

    random.seed(1)
    prices_a = [random.uniform(0, 100) for _ in range(200)]
    prices_b = [random.uniform(0, 100) for _ in range(200)]
    correlation = compute_correlation(prices_a, prices_b)
    assert abs(correlation) < 0.3


def test_compute_hedge_ratio_recovers_known_slope():
    prices_b = [50 + i * 0.5 for i in range(50)]
    prices_a = [10 + 2 * p for p in prices_b]
    hedge_ratio = compute_hedge_ratio(prices_a, prices_b)
    assert abs(hedge_ratio - 2.0) < 0.01


def test_screen_pairs_drops_low_correlation_pairs():
    price_history = {
        "A": [100, 101, 99, 102, 98, 103, 97, 104, 96, 105] * 5,
        "B": [50, 50, 50, 50, 50, 50, 50, 50, 50, 51] * 5,
    }
    results = screen_pairs([("A", "B")], price_history, min_correlation=0.8)
    assert results == []


def test_screen_pairs_keeps_cointegrated_pair():
    import random

    random.seed(0)
    prices_b = [100 + i * 0.1 + random.uniform(-0.5, 0.5) for i in range(300)]
    prices_a = [50 + 2 * p + random.uniform(-1, 1) for p in prices_b]
    price_history = {"A": prices_a, "B": prices_b}

    results = screen_pairs([("A", "B")], price_history, min_correlation=0.8)
    assert len(results) == 1
    assert results[0].symbol_a == "A"
    assert abs(results[0].hedge_ratio - 2.0) < 0.5
