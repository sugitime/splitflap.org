(function (global) {
  const COLOR_BLOCKS = new Set(["🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "⬜"]);
  const EMOJI_RE = /\p{Extended_Pictographic}/u;

  function isColorBlock(ch) {
    return COLOR_BLOCKS.has(ch);
  }

  function isEmojiChar(ch) {
    if (!ch || ch === " " || isColorBlock(ch)) return false;
    return EMOJI_RE.test(ch);
  }

  function normalizeGridChar(ch) {
    if (!ch || ch === " ") return ch;
    if (isColorBlock(ch) || isEmojiChar(ch)) return ch;
    const upper = ch.toUpperCase();
    return upper.length === 1 ? upper : ch;
  }

  function normalizeGridLine(line) {
    return Array.from(line || "").map(normalizeGridChar);
  }

  function usesDirectFlip(oldChar, newChar) {
    if (oldChar === newChar) return true;
    if (isColorBlock(oldChar) || isColorBlock(newChar)) return true;
    if (isEmojiChar(oldChar) || isEmojiChar(newChar)) return true;
    return false;
  }

  global.VestaboardText = {
    isColorBlock,
    isEmojiChar,
    normalizeGridChar,
    normalizeGridLine,
    usesDirectFlip,
  };
})(typeof window !== "undefined" ? window : globalThis);