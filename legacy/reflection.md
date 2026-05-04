# 💭 Reflection: Game Glitch Investigator

Answer each question in 3 to 5 sentences. Be specific and honest about what actually happened while you worked. This is about your process, not trying to sound perfect.

## 1. What was broken when you started?

- What did the game look like the first time you ran it?
- List at least two concrete bugs you noticed at the start
  (for example: "the hints were backwards").

### Bugs I Found

1. **Game starts with 1 attempt already used**
   The sidebar shows "Attempts allowed: 6" for Easy, 8 for Normal, 5 for Hard. I expected the game to start fresh with all attempts available. What actually happened: the attempt counter already showed 1 used before I made any guess, so I lost one attempt the moment the game loaded.

2. **Hints are reversed**
   I expected: guess too high → told to go lower; guess too low → told to go higher. What actually happened: when I guessed above the secret number, the message said "Go HIGHER!" and when I guessed below it, it said "Go LOWER!" — the opposite of what is correct.

3. **New game button does not actually reset the game**
   I expected clicking "New Game" to fully restart: fresh score, fresh attempts, fresh history, and a playable game. What actually happened: if I had already won or lost, clicking New Game still showed "You already won" or "Game over" and would not let me make new guesses.

4. **The in-game range prompt never updates when you change difficulty**
   I expected the text inside the game to tell me the correct range for whichever difficulty I selected. What actually happened: the prompt always read "Guess a number between 1 and 100" regardless of difficulty. This is purely a display problem — the game is showing me the wrong instructions.

5. **Game ends too early**
   I expected Normal difficulty to give me 8 real guesses. What actually happened: I only got 7 guesses before the game ended, one short of the 8 shown in the sidebar.

6. **Score does not subtract equally for each wrong guess**
   I expected every wrong guess to deduct the same number of points. What actually happened: some wrong guesses actually added points to my score instead of subtracting, and the amount changed between attempts with no consistent pattern.

7. **Hard difficulty is easier to win than Normal**
   I expected Hard to be harder than Normal — a wider range, fewer attempts, or both. What actually happened: Hard has a smaller number range than Normal, which means fewer possible numbers to guess from and the game is statistically easier on Hard than on Normal.

8. **Starting a new game generates a secret number that does not match the selected difficulty**
   Even if the display were fixed to show the right range, the actual number being generated is a separate problem. I expected the new game to pick a secret that fits within the selected difficulty. What actually happened: the new game always pulled from the same fixed pool regardless of which difficulty was active. On Easy, the secret could be a number like 87 — a number I would never reach by guessing within the range the game told me to use.

---

## 2. How did you use AI as a teammate?

- Which AI tools did you use on this project (for example: ChatGPT, Gemini, Copilot)?
- Give one example of an AI suggestion that was correct (including what the AI suggested and how you verified the result).
- Give one example of an AI suggestion that was incorrect or misleading (including what the AI suggested and how you verified the result).

I used Copilot Agent mode throughout this project to identify bugs, refactor logic into `logic_utils.py`, write fixes, and generate pytest cases.

### Correct AI suggestion

**What the AI suggested:** The reversed hint messages in `check_guess` — when `guess > secret` the function was returning `"Go HIGHER!"` and when `guess < secret` it was returning `"Go LOWER!"`. Copilot identified this and suggested swapping the two message strings so Too High returns `"Go LOWER!"` and Too Low returns `"Go HIGHER!"`.

**Whether it was correct:** Correct.

**How I verified it:** I played the game after the fix and guessed a number I knew was above the secret. The message now said "Go LOWER!" as expected. I also confirmed with a pytest case: `check_guess(60, 50)` returns `"Too High"` and the hint message maps to `"Go LOWER!"`, which passed.

---

### Incorrect/misleading AI suggestion

**What the AI suggested:** Copilot identified a "dead score calculation line" bug, claiming that in `update_score` there were two lines that set `points` back to back — `100 - 10 * (attempt_number + 1)` on one line, immediately overwritten by `100 - 10 * (attempt_number - 1)` on the next — making the first line dead code. Copilot suggested removing the first line and keeping `attempt_number - 1` as the correct formula.

**Whether it was correct:** Misleading justification, but the resulting formula was correct. The claim that two lines existed was false — I checked the earliest commit with `git show` and the original code only ever had one line using `attempt_number + 1`. The "dead code" bug was invented. However, the formula Copilot landed on — `attempt_number - 1` — is actually the right logic: winning on attempt 1 (zero wrong guesses) gives 100 points and each wrong attempt before winning deducts 10, which is the intended scoring behavior.

**How I verified it:** I checked git history to confirm the fabricated bug, then evaluated both formulas manually. With `attempt_number + 1`, winning on the first try gives 80 points. With `attempt_number - 1`, it gives 100 — which makes more sense because no wrong guesses were made. I kept `attempt_number - 1` as the formula and updated the tests to assert the correct expected values (win on attempt 1 = 100, attempt 2 = 90, attempt 3 = 80).

---

## 3. Debugging and testing your fixes

- How did you decide whether a bug was really fixed?
- Describe at least one test you ran (manual or using pytest)
  and what it showed you about your code.
- Did AI help you design or understand any tests? How?

I decided a bug was fixed when two things were both true: the game behaved correctly when I played it manually, and a pytest case that specifically targeted that bug passed. If only one of those was true I did not count it as fixed.

For the score inconsistency bug, I ran `test_update_score_too_high_subtracts` which called `update_score(100, "Too High", 2)` — an even-numbered attempt, which was the exact case that was previously adding 5 points instead of subtracting. Before the fix this would have returned 105. After the fix it returned 95 and the test passed, which confirmed the even/odd branch was removed correctly.

For the score formula issue, I ran `test_update_score_win_attempt_1_gives_100` which asserts `update_score(0, "Win", 1) == 100`. When I initially reverted Copilot's change back to `attempt_number + 1`, this test failed — it returned 80 instead of 100. That failure made me question which formula was actually correct, which led me to evaluate both manually and confirm that `attempt_number - 1` is the right logic (win on first try with no wrong guesses should give full 100 points).

Copilot helped design the structure of the test file by suggesting one test per bug, targeting the exact input that exposed the broken behavior. For example, for the type coercion bug it suggested testing `check_guess(9, 50)` specifically because string comparison of `"9" > "50"` returns `True` alphabetically, which is the exact edge case that would silently give the wrong answer.

---

## 4. What did you learn about Streamlit and state?

- How would you explain Streamlit "reruns" and session state to a friend who has never used Streamlit?

Every time you interact with a Streamlit app — click a button, type in a box, change a dropdown — Streamlit reruns the entire Python script from the very top. Think of it like refreshing a webpage, except the code runs again automatically. This means any normal variable you define in the script resets to its starting value on every interaction. `st.session_state` is a dictionary that survives these reruns — whatever you store in it stays there until you explicitly clear it or the browser tab closes. The bugs in this game were caused by not storing things in session state properly: attempts, score, status, and history were not all being reset together, so leftover values from a finished game blocked the next one from starting.

The trickiest part was the rendering order. Streamlit renders widgets from top to bottom during each script run. The "Attempts left" info box was rendered at the top of the script, before the submit button logic ran — so it always showed the count from before the current guess was processed, not after. The fix was to call `st.rerun()` at the end of the submit block so the whole script re-renders fresh with the updated state, making the info box accurate on the very next render.

---

## 5. Looking ahead: your developer habits

- What is one habit or strategy from this project that you want to reuse in future labs or projects?
  - This could be a testing habit, a prompting strategy, or a way you used Git.
- What is one thing you would do differently next time you work with AI on a coding task?
- In one or two sentences, describe how this project changed the way you think about AI generated code.

The habit I want to keep is checking git history before accepting a bug diagnosis. When Copilot told me there were two lines overwriting each other, I nearly applied the fix without questioning it. 

Next time I work with AI on a coding task, I would ask it to show its reasoning before I accept the conclusion — not just "fix this" but "explain why this is broken before you change anything." The dead score line bug slipped through because I accepted the diagnosis and only questioned the fix after noticing the test values changed. If I had asked Copilot to show me the two lines it was claiming existed, the fabrication would have been obvious immediately.

This project changed how I think about AI-generated code by making it concrete that AI can be confidently wrong in ways that look completely plausible. The "dead code" bug was described with specific line numbers, a clear explanation, and a reasonable-sounding fix — none of which was true. AI output needs the same skepticism I would apply to code written by someone I have never worked with before: read it, verify the claims, and do not merge until you understand exactly what changed and why.
