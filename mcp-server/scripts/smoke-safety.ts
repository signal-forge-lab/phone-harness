export interface ElementRecord {
  text?: string;
  name?: string;
  role?: string;
}

export function calculatorClearText(elements: ElementRecord[]): string | undefined {
  const byName = new Map(elements.filter((item) => item.name).map((item) => [item.name!, item]));
  const namedClear = byName.get("Clear") ?? byName.get("AllClear");
  if (namedClear?.text && byName.has("One") && byName.has("Equals")) return namedClear.text;

  const clear = namedClear ?? elements.find((item) => item.text === "AC" || item.text === "C");
  const texts = new Set(elements.map((item) => item.text));
  const digitCount = Array.from({ length: 10 }, (_, digit) => String(digit)).filter((digit) => texts.has(digit)).length;
  return clear?.text && digitCount >= 7 ? clear.text : undefined;
}
