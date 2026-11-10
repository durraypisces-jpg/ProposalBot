# FitFlex AI – Your Smart Fitness Buddy

**Category:** AI · **Goal:** SDG 3 – Good Health and Well-being · **Level:** Grade 7 & 8

FitFlex AI is a talking robot fitness buddy. It chats with students, gives health tips, counts
water, tracks exercise, sleep, meals and mood, coaches workouts, and rewards healthy habits with
badges. It runs in any web browser, **works offline and costs nothing**.

## How to run it

1. Copy the `fitflex-ai` folder to a laptop or tablet.
2. Double-click `index.html` (Chrome or Edge works best, for voice and microphone).
3. Type your name and start chatting.

For the robot model: run the page full-screen (F11) on a tablet or old phone placed as the
robot's head. The animated face reacts to every message.

## Features

| Feature | What it does | Try |
|---|---|---|
| 🤖 Emotional robot face | Changes expression: happy, thinking, concerned, celebrating. Mouth moves when talking | Any message |
| 🧠 Brain view | Shows judges *how* the AI understood each message: cleaned words, clues, topic, confidence, action | Any message |
| 🔊 Voice + 🎤 microphone | FitFlex reads replies aloud; students can speak instead of typing (Chrome) | Voice button / 🎤 |
| 🗣️ Roman Urdu | Understands mixed Urdu-English | `maine 2 glass pani piya`, `mujhe neend nahi aati` |
| ✏️ Typo fixing | Understands misspelled words | `excercise`, `slep` |
| 💧 Water tracker | 8-glass counter; a bottle counts as 2 glasses | `I drank 3 glasses`, or tap a glass |
| 🏃 Exercise tracker | Logs activity toward 60 min/day; understands hours | `I played cricket for half an hour` |
| 🏋️ Workout Coach | Guided routine with timer, voice and beeps (Quick 1 min / Full 5 min) | `start workout` |
| 🍽️ Meal checker | Sorts foods into groups, rates the meal with stars, suggests what to add | `I ate roti, daal and an apple` |
| 😴 Sleep tracker | Checks hours against the 8–10 hour teen goal | `I slept 9 hours` |
| 😊 Mood check-in | Asks how you feel and replies with care | Mood check button |
| 📊 Health Score | 0–100 score: water 30, movement 40, sleep 20, mood 10 | `progress` |
| 📈 Weekly chart | 7-day active minutes and water | "Load demo week" for presenting |
| 🎮 XP, levels, badges, streaks | Points for healthy actions, 8 badges, 6 levels | `badges` |
| ⏰ Reminders | Water and move-break reminders with beeps and pop-up alerts | `remind me every 30 minutes` |
| ❓ Health quiz | 10 questions with explanations | `quiz` |
| ⚠️ Safety rule | Words like *pain, fever, bullied, sad, dard* → "talk to a trusted adult". Never gives medical advice | `I have a fever` |

## How we built it (short steps)

1. **Picked the problem:** students drink too little water, sit too much and eat junk food (SDG 3).
2. **Planned the features:** health tips, water reminders and exercise tracking, plus safety.
3. **Designed the screen:** a chat window, a robot face and a health dashboard.
4. **Built the page with HTML** (structure) **and CSS** (colours, layout, robot face animation).
5. **Wrote the chatbot brain in JavaScript:** a list of topics, each with keywords and answers.
6. **Taught it to understand:** clean the message → check safety words → look for actions with numbers →
   score every topic by keywords (with word families and typo fixing) → pick the best topic.
7. **Added memory:** progress is saved in the browser (`localStorage`), and a new day starts fresh.
8. **Added smart extras:** Health Score, Workout Coach timer, meal checker, voice, badges and a chart.
9. **Tested it:** tried many messages (English, Roman Urdu, typos, tricky ones), fixed mistakes.
10. **Made it explainable:** added the Brain view so anyone can see how the AI decides.

## How the AI works (for the presentation)

FitFlex uses **Natural Language Processing (NLP)** with rules:

1. **Input** – typed or spoken message.
2. **Cleaning** – lowercase, punctuation removed, split into words.
3. **Safety check** – serious words always go to a trusted adult first.
4. **Entity extraction** – finds numbers and units (“20 minutes”, “half an hour”, “2 glasses”), foods and activities.
5. **Intent scoring** – each topic gets points for matching keywords; typos are fixed with
   *edit distance* (how many letters differ). The highest score wins, and confidence is shown.
6. **Response + action** – reply, update the dashboard, give XP, change the robot's face, speak.

Limitation (judges like honesty): it only understands what it was taught. Real AI like ChatGPT
learns language from billions of sentences. Future version: connect a real AI model, add Urdu replies.

## Tips to win the competition

**2-minute demo script**
1. *Problem (20 s):* "Many students don't drink enough water, don't move enough and skip breakfast."
2. *Solution (10 s):* "FitFlex AI is a smart fitness buddy that makes healthy habits fun."
3. *Live demo (60 s):* turn on Voice → type your name → `maine 2 glass pani piya` →
   `I ate chips and pepsi` (1 star) then `I ate roti, daal and an apple` (5 stars + badge) →
   Workout Coach → Quick → show the Brain view → `I have a fever` (safety) → `progress`.
4. *Impact (20 s):* "It works offline and free, so every school can use it. It protects privacy because data stays on the device."
5. *Future (10 s):* "Urdu voice, a step counter with a micro:bit, and a class leaderboard."

**Before presenting**
- Click **Load demo week** so the chart is full. Set water reminder to **1 min (demo)** so judges hear it.
- Show a real survey: ask 30 classmates how many glasses of water they drink and how many minutes they exercise. Put the results on the poster. Real data impresses judges.
- Each team member should explain one part: the face, the brain, the trackers, the safety rule.
- On the poster, show the 6-step "How the AI works" flow and the SDG 3 logo.

## How students can improve it

Open `index.html` in Notepad or VS Code. The knowledge is at the top of the `<script>` section:

- **`BRAIN`** – add a topic: copy one block and change the keywords and answers.
- **`TIPS`**, **`QUIZ`** – add tips and quiz questions.
- **`FOODS`** – add local foods to the meal checker (e.g. `"biryani"` in `grain`).
- **`SAFETY_WORDS`** – words that send the student to a trusted adult.
- **`ROUTINES`** – design your own workout.
- **`BADGES`** – invent new badges.

## Health facts used

- Children and teens (5–17) need **60 minutes** of moderate-to-vigorous activity daily (WHO).
- Teens (13–18) need **8–10 hours** of sleep (American Academy of Sleep Medicine).
- Wash hands with soap for **20 seconds** (WHO / CDC).
- Half the plate fruit and vegetables (healthy plate guidelines).
- About **6–8 glasses** of water a day is a common guide for this age, more in hot weather or after sport.
- A 330 ml can of soda has about **35 g (≈8 teaspoons)** of sugar.

## Notes

- The microphone needs Chrome or Edge and an internet connection (the browser's speech service). Everything else works offline.
- Data is stored only in that browser. "Erase all data" clears it.
