from logic_utils import check_guess, update_score, get_range_for_difficulty, reset_game, parse_guess

def test_winning_guess():
    # If the secret is 50 and guess is 50, it should be a win
    result = check_guess(50, 50)
    assert result == "Win"

def test_guess_too_high():
    # If secret is 50 and guess is 60, hint should be "Too High"
    result = check_guess(60, 50)
    assert result == "Too High"

def test_guess_too_low():
    # If secret is 50 and guess is 40, hint should be "Too Low"
    result = check_guess(40, 50)
    assert result == "Too Low"


# --- Hints were reversed ---

def test_check_guess_too_high_returns_correct_outcome():
    # guess above secret must return Too High, not Too Low
    assert check_guess(60, 50) == "Too High"

def test_check_guess_too_low_returns_correct_outcome():
    # guess below secret must return Too Low, not Too High
    assert check_guess(40, 50) == "Too Low"

def test_check_guess_correct():
    assert check_guess(50, 50) == "Win"


# --- Type coercion bug: check_guess must work correctly with two ints ---

def test_check_guess_no_string_coercion_high():
    # 9 vs 50 — string comparison "9" > "50" is True, numeric is False
    # must return Too Low, not Too High
    assert check_guess(9, 50) == "Too Low"

def test_check_guess_no_string_coercion_low():
    assert check_guess(50, 9) == "Too High"


# --- Attempts must start at 0 ---

def test_reset_game_attempts_start_at_zero():
    state = reset_game(1, 100)
    assert state["attempts"] == 0


# --- New game resets all state ---

def test_reset_game_clears_score():
    state = reset_game(1, 100)
    assert state["score"] == 0

def test_reset_game_clears_status():
    state = reset_game(1, 100)
    assert state["status"] == "playing"

def test_reset_game_clears_history():
    state = reset_game(1, 100)
    assert state["history"] == []


# --- reset_game secret stays within the difficulty range ---

def test_reset_game_secret_within_easy_range():
    for _ in range(50):
        state = reset_game(1, 20)
        assert 1 <= state["secret"] <= 20

def test_reset_game_secret_within_hard_range():
    for _ in range(50):
        state = reset_game(1, 200)
        assert 1 <= state["secret"] <= 200


# --- Score deducts equally for both wrong guess types ---

def test_update_score_too_high_subtracts():
    # must subtract 5, never add
    assert update_score(100, "Too High", 1) == 95
    assert update_score(100, "Too High", 2) == 95  # even attempt — was adding 5 before fix

def test_update_score_too_low_subtracts():
    assert update_score(100, "Too Low", 1) == 95
    assert update_score(100, "Too Low", 2) == 95

def test_update_score_wrong_guess_consistent():
    # both directions must produce the same deduction
    assert update_score(100, "Too High", 3) == update_score(100, "Too Low", 3)


# --- Win on attempt 1 should give 100 points ---

def test_update_score_win_attempt_1_gives_100():
    # 100 - 10 * (1 - 1) = 100
    assert update_score(0, "Win", 1) == 100

def test_update_score_win_decreases_with_attempts():
    # each extra attempt reduces the win bonus by 10
    assert update_score(0, "Win", 2) == 90
    assert update_score(0, "Win", 3) == 80

def test_update_score_win_minimum_10_points():
    # score should never drop below 10 for a win
    assert update_score(0, "Win", 100) == 10


# --- Hard difficulty must be harder (wider range) than Normal ---

def test_hard_range_wider_than_normal():
    normal_low, normal_high = get_range_for_difficulty("Normal")
    hard_low, hard_high = get_range_for_difficulty("Hard")
    assert (hard_high - hard_low) > (normal_high - normal_low)

def test_easy_range_narrower_than_normal():
    normal_low, normal_high = get_range_for_difficulty("Normal")
    easy_low, easy_high = get_range_for_difficulty("Easy")
    assert (easy_high - easy_low) < (normal_high - normal_low)

def test_get_range_easy():
    assert get_range_for_difficulty("Easy") == (1, 20)

def test_get_range_normal():
    assert get_range_for_difficulty("Normal") == (1, 100)

def test_get_range_hard():
    low, high = get_range_for_difficulty("Hard")
    assert high > 100  # must exceed Normal's upper bound


# --- Edge case 1: Negative numbers ---
# A player typing "-5" gets a valid integer back, but it is outside every difficulty range.
# parse_guess should accept it as a number (not an error), and check_guess should handle it correctly.

def test_parse_guess_negative_number_is_accepted():
    # "-5" is a valid integer — parse_guess should not reject it
    ok, value, err = parse_guess("-5")
    assert ok is True
    assert value == -5
    assert err is None

def test_check_guess_negative_is_always_too_low():
    # any negative guess is below any realistic secret
    assert check_guess(-5, 1) == "Too Low"
    assert check_guess(-100, 50) == "Too Low"


# --- Edge case 2: Decimal inputs ---
# A player typing "3.9" could silently truncate to 3 and win if the secret is 3.
# parse_guess must reject all decimal inputs so players cannot accidentally win with a wrong number.

def test_parse_guess_decimal_rejected():
    # "3.9" must be rejected — truncating to 3 could give a false win
    ok, value, err = parse_guess("3.9")
    assert ok is False
    assert value is None
    assert err is not None

def test_parse_guess_negative_decimal_rejected():
    # "-3.9" must also be rejected
    ok, value, err = parse_guess("-3.9")
    assert ok is False
    assert value is None
    assert err is not None

def test_parse_guess_whole_number_as_decimal_rejected():
    # even "5.0" must be rejected — players must type 5, not 5.0
    ok, value, err = parse_guess("5.0")
    assert ok is False
    assert value is None
    assert err is not None


# --- Edge case 3: Scientific notation and non-numeric strings ---
# "1e5" looks like a number but int() cannot parse it directly.
# Strings like "abc" or " " should always return an error, never crash.

def test_parse_guess_scientific_notation_rejected():
    # "1e5" has no "." so it goes to int("1e5") which raises ValueError
    ok, value, err = parse_guess("1e5")
    assert ok is False
    assert value is None
    assert err is not None

def test_parse_guess_letters_rejected():
    ok, value, _ = parse_guess("abc")
    assert ok is False
    assert value is None

def test_parse_guess_whitespace_rejected():
    ok, value, _ = parse_guess("   ")
    assert ok is False
    assert value is None

def test_parse_guess_empty_string_rejected():
    ok, value, _ = parse_guess("")
    assert ok is False
    assert value is None

def test_parse_guess_none_rejected():
    ok, value, _ = parse_guess(None)
    assert ok is False
    assert value is None

def test_parse_guess_extremely_large_number():
    # very large numbers should parse without crashing
    ok, value, _ = parse_guess("999999999")
    assert ok is True
    assert value == 999999999
