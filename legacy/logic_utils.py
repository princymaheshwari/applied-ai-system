import random


# FIX: Moved from app.py into logic_utils.py using Copilot Agent mode
# FIX: Hard range corrected from 1-50 to 1-200 so it is harder than Normal using Copilot Agent mode
def get_range_for_difficulty(difficulty: str):
    """Return (low, high) inclusive range for a given difficulty."""
    if difficulty == "Easy":
        return 1, 20
    if difficulty == "Normal":
        return 1, 100
    if difficulty == "Hard":
        return 1, 200
    return 1, 100


# FIX: Created to replace broken new game block that only reset attempts and secret using Copilot Agent mode
# FIX: Now resets all state (secret, attempts, score, status, history) and generates secret within correct range using Copilot Agent mode
def reset_game(low: int, high: int) -> dict:
    """Return a fresh game state for a new game within the given range."""
    return {
        "secret": random.randint(low, high),
        "attempts": 0,
        "score": 0,
        "status": "playing",
        "history": [],
    }


# FIX: Moved from app.py into logic_utils.py using Copilot Agent mode
def parse_guess(raw: str):
    """
    Parse user input into an int guess.

    Returns: (ok: bool, guess_int: int | None, error_message: str | None)
    """
    if raw is None:
        return False, None, "Enter a guess."

    if raw == "":
        return False, None, "Enter a guess."

    try:
        if "." in raw:
            return False, None, "Please enter a whole number."
        value = int(raw)
    except Exception:
        return False, None, "That is not a number."

    return True, value, None


# FIX: Moved from app.py into logic_utils.py using Copilot Agent mode
# FIX: Reversed hint messages corrected — Too High now says Go LOWER, Too Low says Go HIGHER using Copilot Agent mode
# FIX: Removed type coercion that cast secret to str on even attempts causing unreliable string comparison using Copilot Agent mode
def check_guess(guess, secret):
    """
    Compare guess to secret and return the outcome string.

    outcome: "Win", "Too High", or "Too Low"
    """
    if guess == secret:
        return "Win"

    try:
        if guess > secret:
            return "Too High"
        else:
            return "Too Low"
    except TypeError:
        g = str(guess)
        if g == secret:
            return "Win"
        if g > secret:
            return "Too High"
        return "Too Low"


# FIX: Moved from app.py into logic_utils.py using Copilot Agent mode
# FIX: Removed even/odd branch that added 5 points on even Too High attempts — now always subtracts 5 using Copilot Agent mode
def update_score(current_score: int, outcome: str, attempt_number: int):
    """Update score based on outcome and attempt number."""
    if outcome == "Win":
        points = 100 - 10 * (attempt_number - 1)
        if points < 10:
            points = 10
        return current_score + points

    if outcome in ("Too High", "Too Low"):
        return current_score - 5

    return current_score
