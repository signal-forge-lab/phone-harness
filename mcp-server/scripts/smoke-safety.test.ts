import assert from "node:assert/strict";
import test from "node:test";
import { calculatorClearText } from "./smoke-safety.js";

test("accepts calculator OCR text when accessibility names are absent", () => {
  const clear = calculatorClearText([
    { text: "AC" },
    ...Array.from({ length: 7 }, (_, digit) => ({ text: String(digit) })),
  ]);
  assert.equal(clear, "AC");
});

test("keeps accessibility-name calculator detection", () => {
  const clear = calculatorClearText([
    { name: "AllClear", text: "AC" },
    { name: "One", text: "1" },
    { name: "Equals", text: "=" },
  ]);
  assert.equal(clear, "AC");
});

test("rejects unrelated OCR text", () => {
  assert.equal(
    calculatorClearText([
      { text: "AC" },
      ...Array.from({ length: 6 }, (_, digit) => ({ text: String(digit) })),
    ]),
    undefined,
  );
});
