import random
import streamlit as st
# FIX: All game logic functions moved out of app.py into logic_utils.py using Copilot Agent mode
from logic_utils import check_guess, reset_game, update_score, get_range_for_difficulty, parse_guess


st.set_page_config(page_title="Glitchy Guesser", page_icon="🎮")

st.title("🎮 Game Glitch Investigator")
st.caption("An AI-generated guessing game. Something is off.")

st.sidebar.header("Settings")

difficulty = st.sidebar.selectbox(
    "Difficulty",
    ["Easy", "Normal", "Hard"],
    index=1,
)

attempt_limit_map = {
    "Easy": 6,
    "Normal": 8,
    "Hard": 5,
}
attempt_limit = attempt_limit_map[difficulty]

low, high = get_range_for_difficulty(difficulty)

st.sidebar.caption(f"Range: {low} to {high}")
st.sidebar.caption(f"Attempts allowed: {attempt_limit}")

if "secret" not in st.session_state:
    st.session_state.secret = random.randint(low, high)

# FIX: Changed from 1 to 0 so players get their full attempt count using Copilot Agent mode
if "attempts" not in st.session_state:
    st.session_state.attempts = 0

if "score" not in st.session_state:
    st.session_state.score = 0

if "status" not in st.session_state:
    st.session_state.status = "playing"

if "history" not in st.session_state:
    st.session_state.history = []

if "last_hint" not in st.session_state:
    st.session_state.last_hint = None

if "show_balloons" not in st.session_state:
    st.session_state.show_balloons = False

# FIX: game_count in the input key forces a fresh empty widget on new game without modifying widget state directly using Copilot Agent mode
if "game_count" not in st.session_state:
    st.session_state.game_count = 0

if "difficulty" not in st.session_state:
    st.session_state.difficulty = difficulty

# FIX: Detects difficulty change and resets game so secret is always within the selected difficulty range using Copilot Agent mode
if st.session_state.difficulty != difficulty:
    fresh = reset_game(low, high)
    for key, value in fresh.items():
        st.session_state[key] = value
    st.session_state.difficulty = difficulty
    st.session_state.last_hint = None
    st.session_state.show_balloons = False
    st.rerun()

st.subheader("Make a guess")

# FIX: Replaced hardcoded "1 and 100" with {low} and {high} to show correct range per difficulty using Copilot Agent mode
st.info(
    f"Guess a number between {low} and {high}. "
    f"Attempts left: {attempt_limit - st.session_state.attempts}"
)

with st.expander("Developer Debug Info"):
    st.write("Secret:", st.session_state.secret)
    st.write("Attempts:", st.session_state.attempts)
    st.write("Score:", st.session_state.score)
    st.write("Difficulty:", difficulty)
    st.write("History:", st.session_state.history)

raw_guess = st.text_input(
    "Enter your guess:",
    key=f"guess_input_{difficulty}_{st.session_state.game_count}"
)

col1, col2, col3 = st.columns(3)
with col1:
    submit = st.button("Submit Guess 🚀")
with col2:
    new_game = st.button("New Game 🔁")
with col3:
    show_hint = st.checkbox("Show hint", value=True, key="show_hint_checkbox")

# FIX: New game now resets all state (status, score, history) not just attempts and secret using Copilot Agent mode
if new_game:
    fresh = reset_game(low, high)
    for key, value in fresh.items():
        st.session_state[key] = value
    st.session_state.game_count += 1
    st.session_state.last_hint = None
    st.session_state.show_balloons = False
    st.rerun()

if st.session_state.last_hint:
    st.warning(st.session_state.last_hint)

if st.session_state.status == "won":
    if st.session_state.show_balloons:
        st.balloons()
        st.session_state.show_balloons = False
    st.success(
        f"You won! The secret was {st.session_state.secret}. "
        f"Final score: {st.session_state.score}"
    )
    st.stop()

if st.session_state.status == "lost":
    st.error(
        f"Out of attempts! "
        f"The secret was {st.session_state.secret}. "
        f"Score: {st.session_state.score}"
    )
    st.stop()

# FIX: Submit stores hint in session state and calls st.rerun() so info box and debug always show updated state using Copilot Agent mode
if submit:
    st.session_state.last_hint = None
    st.session_state.attempts += 1

    ok, guess_int, err = parse_guess(raw_guess)

    if not ok:
        st.session_state.history.append(raw_guess)
        st.session_state.last_hint = err
    else:
        st.session_state.history.append(guess_int)

        outcome = check_guess(guess_int, st.session_state.secret)

        hint_messages = {
            "Win": "🎉 Correct!",
            "Too High": "📉 Go LOWER!",
            "Too Low": "📈 Go HIGHER!",
        }
        if show_hint:
            st.session_state.last_hint = hint_messages[outcome]

        st.session_state.score = update_score(
            current_score=st.session_state.score,
            outcome=outcome,
            attempt_number=st.session_state.attempts,
        )

        if outcome == "Win":
            st.session_state.status = "won"
            st.session_state.show_balloons = True
        elif st.session_state.attempts >= attempt_limit:
            st.session_state.status = "lost"

    st.rerun()

st.divider()
st.caption("Built by an AI that claims this code is production-ready.")
