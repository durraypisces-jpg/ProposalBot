# FitFlex AI – Your Smart Fitness Buddy

**Category:** AI · **Goal:** SDG 3 – Good Health and Well-being · **Level:** Grade 7 & 8

FitFlex AI is a chatbot that helps students live healthier lives. It gives health tips,
reminds them to drink water and tracks their exercise.

## How to run it

1. Download or copy the `fitflex-ai` folder.
2. Double-click `index.html`. It opens in Chrome, Edge or Firefox.
3. Start chatting. No internet, account or API key is needed.

For the robot model, play it full-screen on a tablet or old phone placed as the robot's "face",
or on a laptop next to the model.

## What FitFlex can do

| Feature | Try typing |
|---|---|
| Learns your name | `My name is Ali` |
| Health tips | `tip`, `what should I eat for breakfast`, `I can't sleep` |
| Water counter | `I drank a glass`, `I drank 3 glasses` (or tap the glasses) |
| Exercise tracker (goal: 60 min/day) | `I ran for 20 minutes`, `I played cricket for 1 hour` |
| Workouts and stretches | `give me a workout`, `stretch` |
| Water reminders with a beep | `remind me every 30 minutes` (choose "1 min (demo)" for judges) |
| Health quiz | `quiz`, then `A`, `B` or `C` |
| Daily progress report | `progress` |
| Safety | Words like *pain*, *dizzy*, *bullied* or *sad* make FitFlex tell the student to talk to a trusted adult |

Progress is saved in the browser and resets each new day.

## How the "AI" works (for the presentation)

FitFlex is a **rule-based chatbot**, the same idea early chatbots like ELIZA used:

1. **Input:** the student types a message.
2. **Clean up:** the message is changed to lowercase and punctuation is removed.
3. **Understand:** FitFlex looks for *keywords* (like `water`, `sleep`, `football`) and *numbers* (like `20 minutes`).
4. **Decide:** the topic with the most matching keywords wins. Safety words are always checked first.
5. **Reply:** FitFlex picks one of the answers for that topic, and updates the water/exercise trackers.

Good points for the judges:
- It works **offline**, so any school can use it for free.
- It keeps student data **on the device only**.
- It has a **safety rule**: it never gives medical advice and sends serious problems to a trusted adult.
- Limitation: it only understands words it was taught. Real AI chatbots learn language from huge amounts of text.

## How students can improve it (open `index.html` in Notepad or VS Code)

All the knowledge lives near the top of the `<script>` section:

- **`BRAIN`**: add a new topic. Copy one block and change it:
  ```js
  { topic: "posture",
    keywords: ["posture", "slouch", "sit straight"],
    answers: ["Sit with your back straight and feet flat on the floor! 🪑"] },
  ```
- **`TIPS`**: add more tips of the day.
- **`QUIZ`**: add more quiz questions (`answer` is `"a"`, `"b"` or `"c"`).
- **`SAFETY_WORDS`**: words that send the student to a trusted adult.
- Urdu or Roman-Urdu keywords can be added too (e.g. `"pani"` for water, `"neend"` for sleep).

Ideas for extra marks: add an Urdu mode, a step counter, a sleep tracker, a healthy-meal planner,
or a weekly chart of active minutes.

## Health facts used

- Children and teens (5–17) need **60 minutes** of moderate-to-vigorous activity daily (WHO).
- Teens need about **8–10 hours** of sleep (CDC / American Academy of Sleep Medicine).
- Wash hands with soap for **20 seconds** (WHO / CDC).
- Water needs vary; about **6–8 glasses** a day is a common guide for this age, more in hot weather or after sport.
