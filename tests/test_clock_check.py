import pytest

from loadtest.clock_check import estimate_offset_s


def test_offset_positive_when_local_clock_is_behind():
    # Servidor 1000 (truncado; real ~1000,5), equipo en el medio del intercambio = 997,0
    assert estimate_offset_s([(996.9, 997.1, 1000)]) == pytest.approx(3.5)


def test_offset_is_median_of_observations():
    obs = [(0.0, 0.0, 10), (0.0, 0.0, 11), (0.0, 0.0, 200)]
    assert estimate_offset_s(obs) == pytest.approx(11.5)


def test_offset_requires_observations():
    with pytest.raises(ValueError):
        estimate_offset_s([])


def test_sntp_offset_positive_when_local_clock_is_behind():
    # equipo atrasado 0.2 s; ida 0.1 s, proceso en servidor 0.01 s, vuelta 0.1 s
    from loadtest.clock_check import sntp_offset_s

    assert sntp_offset_s(t1=100.0, t2=100.3, t3=100.31, t4=100.21) == pytest.approx(0.2)


def test_sntp_offset_negative_when_local_clock_is_ahead():
    from loadtest.clock_check import sntp_offset_s

    assert sntp_offset_s(t1=100.18, t2=100.1, t3=100.11, t4=100.39) == pytest.approx(-0.18, abs=1e-9)


def test_sntp_offset_is_independent_of_symmetric_network_delay():
    from loadtest.clock_check import sntp_offset_s

    assert sntp_offset_s(10.0, 10.05, 10.05, 10.1) == pytest.approx(sntp_offset_s(10.0, 10.5, 10.5, 11.0))


def test_pick_best_reading_prefers_the_lowest_uncertainty_source():
    from loadtest.clock_check import ClockReading, pick_best_reading

    slow = ClockReading(offset_s=-0.21, method="sntp", uncertainty_s=0.075)
    fast = ClockReading(offset_s=-0.20, method="sntp", uncertainty_s=0.04)
    assert pick_best_reading([slow, fast]) is fast
    assert pick_best_reading([]) is None
