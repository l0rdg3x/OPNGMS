import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { renderWithProviders } from "../test/utils";

describe("App", () => {
  it("renders the app name", () => {
    renderWithProviders(<App />);
    expect(screen.getByText("OPNGMS")).toBeInTheDocument();
  });
});
