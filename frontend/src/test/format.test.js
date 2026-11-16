import { describe, expect, it } from "vitest";
import { displayValue, formatNumber, unitLabel, unitTitle } from "../lib/format.js";

describe("formatNumber", () => {
  it("groups digits without float conversion and keeps every decimal", () => {
    expect(formatNumber("481641954")).toBe("48,16,41,954");
    expect(formatNumber("1266108.110")).toBe("12,66,108.110");
    expect(formatNumber("0.0000874155")).toBe("0.0000874155");
    expect(formatNumber("-5")).toBe("-5");
  });
  it("returns null for missing values, never 0", () => {
    expect(formatNumber("")).toBeNull();
    expect(formatNumber(null)).toBeNull();
    expect(formatNumber(undefined)).toBeNull();
  });
});

describe("unit labels", () => {
  it.each([
    ["GJ", false, "GJ"], ["Non-SI:MJ", true, "MJ"], ["tCO2e", false, "tCO₂e"],
    ["Non-SI:MtCO2e", true, "MtCO₂e"], ["GJ/INR", false, "GJ per ₹"],
    ["Non-SI:GJ / iso4217:INR", true, "GJ per ₹"], ["INR", false, "₹"],
    ["iso4217:INR", true, "₹"], ["xbrli:pure", true, "no unit stated"],
    ["pure", false, "number"], ["kL", false, "kL"], ["%", false, "%"],
  ])("%s (filed=%s) -> %s", (unit, filed, expected) => {
    expect(unitLabel(unit, filed)).toBe(expected);
  });
  it("keeps the exact unit code in the tooltip", () => {
    expect(unitTitle("Non-SI:MtCO2e")).toMatch(/^Non-SI:MtCO2e — .*ambiguous/);
    expect(unitTitle("GJ")).toBe("GJ");
  });
});

describe("displayValue", () => {
  it("uses the normalised value when there is one", () => {
    expect(displayValue({ value: "10", value_numeric: 10, unit: "GJ" })).toMatchObject({ text: "10", unit: "GJ", missing: false });
  });
  it("falls back to the filed value, labelled as filed", () => {
    const d = displayValue({ value: null, value_numeric: null, reported_value: "473590585", reported_unit: "xbrli:pure" });
    expect(d).toMatchObject({ text: "47,35,90,585", unit: "no unit stated", asFiled: true, missing: false });
  });
  it("never turns a missing value into zero", () => {
    expect(displayValue({ value: null, value_numeric: null })).toMatchObject({ text: "Not available", missing: true });
  });
  it("shows a filed zero as 0", () => {
    expect(displayValue({ value: "0", value_numeric: 0, unit: "count" }).text).toBe("0");
  });
});
