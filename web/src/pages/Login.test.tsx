import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: { config: vi.fn(), login: vi.fn(), me: vi.fn(), webappLogin: vi.fn() } }));

import { api } from "@/api/client";
import { Login } from "./Login";

const widget = () => document.querySelector("script[data-telegram-login]");

beforeEach(() => {
  vi.mocked(api.me).mockRejectedValue(new Error("401"));   // anonymous unless a test says otherwise
  window.location.hash = "";
  delete window.Telegram;
});

const inTelegram = (initData = "auth_date=1&user=%7B%22id%22%3A7%7D&hash=abc") => {
  window.Telegram = { WebApp: { initData, colorScheme: "light", ready: vi.fn(), expand: vi.fn() } };
};

test("names the bot from the server, not from the bundle", async () => {
  vi.mocked(api.config).mockResolvedValue({ bot_username: "cladex_bot" });
  render(<MemoryRouter><Login /></MemoryRouter>);
  await waitFor(() => expect(widget()).not.toBeNull());
  expect(widget()!.getAttribute("data-telegram-login")).toBe("cladex_bot");
});

test("says so instead of rendering a widget nobody can use", async () => {
  vi.mocked(api.config).mockResolvedValue({ bot_username: "" });
  render(<MemoryRouter><Login /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(widget()).toBeNull();
});

test("inside Telegram, signs in with initData and never renders the widget", async () => {
  vi.mocked(api.config).mockResolvedValue({ bot_username: "cladex_bot" });
  vi.mocked(api.webappLogin).mockResolvedValue({ user_id: 7, role: "owner" });
  // anonymous on the page's first look, then the full Me once the login set the cookie
  vi.mocked(api.me).mockRejectedValueOnce(new Error("401"))
    .mockResolvedValue({ user_id: 7, role: "owner", default_model: "claude-opus-5-5", default_effort: null });
  inTelegram();
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<p>chat</p>} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText("chat")).toBeInTheDocument();
  expect(api.webappLogin).toHaveBeenCalledWith("auth_date=1&user=%7B%22id%22%3A7%7D&hash=abc");
  expect(window.Telegram!.WebApp!.ready).toHaveBeenCalled();
  expect(window.Telegram!.WebApp!.expand).toHaveBeenCalled();
  expect(widget()).toBeNull();
});

test("reads initData off the fragment when the bridge script has not landed", async () => {
  vi.mocked(api.config).mockResolvedValue({ bot_username: "cladex_bot" });
  vi.mocked(api.webappLogin).mockResolvedValue({ user_id: 7, role: "owner" });
  // anonymous on the page's first look, then the full Me once the login set the cookie
  vi.mocked(api.me).mockRejectedValueOnce(new Error("401"))
    .mockResolvedValue({ user_id: 7, role: "owner", default_model: "claude-opus-5-5", default_effort: null });
  window.location.hash = "#tgWebAppData=auth_date%3D1%26hash%3Dabc&tgWebAppVersion=8.0";
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<p>chat</p>} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText("chat")).toBeInTheDocument();
  expect(api.webappLogin).toHaveBeenCalledWith("auth_date=1&hash=abc");
});

test("inside Telegram, a refused account sees the error and no widget", async () => {
  vi.mocked(api.config).mockResolvedValue({ bot_username: "cladex_bot" });
  vi.mocked(api.webappLogin).mockRejectedValue(new Error("401"));
  inTelegram();
  render(<MemoryRouter><Login /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(widget()).toBeNull();
});

test("sends a visitor who is already logged in straight to the chat", async () => {
  vi.mocked(api.config).mockResolvedValue({ bot_username: "cladex_bot" });
  vi.mocked(api.me).mockResolvedValue({ user_id: 7, role: "owner", default_model: null, default_effort: null });
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<p>chat</p>} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText("chat")).toBeInTheDocument();
});
