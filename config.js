const SITE_NAME = "The Daily";
// Doubles as the fallback masthead title -- rendered as-is whenever the
// time-of-day greeting below can't be computed for any reason.

// The one place the reader's name is configured -- change it here, not in
// the phrase pools themselves, so it never needs editing in more than one
// spot. See app.js's greetingForMasthead() for how these are used: a
// deterministic (local date + daypart) pick, weighted per entry, never
// true runtime randomness.
const USER_NAME = "Bruno";

const DAYPART_GREETINGS = {
  morning: [
    { text: `Good Morning, ${USER_NAME}`, weight: 60 },
    { text: "Your Morning Edition", weight: 15 },
    { text: `Start Here, ${USER_NAME}`, weight: 15 },
    { text: "While You Were Sleeping", weight: 10 },
  ],
  afternoon: [
    { text: `Good Afternoon, ${USER_NAME}`, weight: 60 },
    { text: "The Day So Far", weight: 20 },
    { text: "Here's Where Things Stand", weight: 10 },
    { text: "Afternoon Edition", weight: 10 },
  ],
  evening: [
    { text: `Good Evening, ${USER_NAME}`, weight: 60 },
    { text: "The Day in Review", weight: 15 },
    { text: "Before the Day Ends", weight: 15 },
    { text: `Tonight, ${USER_NAME}`, weight: 10 },
  ],
  "late-night": [
    { text: "Late Edition", weight: 50 },
    { text: "Before Tomorrow", weight: 20 },
    { text: "One Last Look", weight: 15 },
    { text: `Still Up, ${USER_NAME}?`, weight: 15 },
  ],
};

// Single editorial-rules config for the frontend: section order, how long an
// article has to be to earn the long-read band, and the deterministic source
// priority/type used to pick the hero and to pull an analysis lead out of a
// dense radar section. Nothing here is an LLM judgment call -- every value
// is a plain number or label anyone can edit by hand.
const LONGREAD_WORD_THRESHOLD = 300;

// Higher wins when picking the hero. Sources not listed fall back to
// DEFAULT_SOURCE_PRIORITY -- a new "news" label works without an edit here,
// it just won't outrank anything.
const SOURCE_PRIORITY = {
  "Reuters": 3,
  "Reuters Daily Briefing": 3,
  "The Conversation": 3,
  "The Conversation AI": 3,
  "Stratechery": 4,
  "Interconnects": 4,
  "Interconnects by Nathan Lambert": 4,
  "Quanta": 4,
  "Quanta Magazine": 4,
  "The New Yorker": 4,
  "New Yorker Books": 4,
  "Works in Progress": 3,
  "Azeem Azhar": 3,
  "Azeem Azhar, Exponential View": 3,
  "Exponential View": 3,
  "Francesco Costa": 3,
  "Da Costa a Costa": 3,
  "Razib Khan": 3,
  "Razib Khan's Unsupervised Learning": 3,
  "Il Post": 2,
  "Il Post - Colonne": 2,
  "Il Post - Ok Boomer!": 2,
  "Water & Music": 2,
  "Water and Music": 2,
  "Music Business Worldwide": 2,
  "The AI Musicpreneur": 2,
  "TLDR": 1,
  "TLDR AI": 1,
};
const DEFAULT_SOURCE_PRIORITY = 1;

// What kind of writing a source usually is. Used only to nudge the hero pick
// and to give one analysis piece room to breathe above the Technology & AI
// radar grid -- never to change what section an article lands in (that's
// still SECTION_MAP in fetch_newsletters.py).
const SOURCE_TYPE = {
  "TLDR": "radar",
  "TLDR AI": "radar",
  "Reuters": "radar",
  "Reuters Daily Briefing": "radar",
  "Stratechery": "analysis",
  "Interconnects": "analysis",
  "Interconnects by Nathan Lambert": "analysis",
  "Azeem Azhar": "analysis",
  "Azeem Azhar, Exponential View": "analysis",
  "Exponential View": "analysis",
  "The Conversation": "analysis",
  "The Conversation AI": "analysis",
  "Razib Khan": "analysis",
  "Razib Khan's Unsupervised Learning": "analysis",
  "Quanta": "long-form",
  "Quanta Magazine": "long-form",
  "The New Yorker": "long-form",
  "New Yorker Books": "long-form",
  "Works in Progress": "long-form",
  "Francesco Costa": "long-form",
  "Da Costa a Costa": "long-form",
  "Il Post": "culture",
  "Il Post - Colonne": "culture",
  "Il Post - Ok Boomer!": "culture",
  "Water & Music": "music",
  "Water and Music": "music",
  "Music Business Worldwide": "music",
  "The AI Musicpreneur": "music",
};
const DEFAULT_SOURCE_TYPE = "radar";

// Small, deliberately capped bonus by type -- a strong analysis/long-form
// piece can outrank a newer radar item, but the gap is never so wide that
// source priority stops mattering.
const TYPE_HERO_BONUS = {
  "long-form": 3,
  "analysis": 2,
  "culture": 1,
  "music": 1,
  "radar": 0,
};

// Image availability improves presentation but must never be the only
// reason an item becomes the hero -- kept small relative to priority/type.
const IMAGE_HERO_BONUS = 1;
