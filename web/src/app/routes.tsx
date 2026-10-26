/** Route table: login is public, everything else sits behind the auth gate.
 *
 * `/routines` and `/routines/:id` are from before the calendar and stay as
 * redirects: bookmarks, the Telegram menu and links in old messages still point
 * there. */

import { Navigate, createBrowserRouter } from "react-router";
import { AuthGate } from "./AuthGate";
import { Layout } from "./Layout";
import { Login } from "@/pages/Login";
import { Chat } from "@/pages/Chat";
import { Routines } from "@/pages/Routines";
import { Routine } from "@/pages/Routine";
import { Calendar, RoutineRedirect } from "@/pages/Calendar";
import { CalendarBoard } from "@/components/cladex/calendar/CalendarBoard";
import { Memory } from "@/pages/Memory";
import { Customize } from "@/pages/Customize";
import { ConnectorsTab } from "@/components/cladex/ConnectorsTab";
import { ConnectorPage } from "@/pages/ConnectorPage";
import { PluginsTab } from "@/components/cladex/PluginsTab";
import { PluginPage } from "@/pages/PluginPage";
import { SkillsTab } from "@/components/cladex/SkillsTab";
import { SkillPage } from "@/pages/SkillPage";

export const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  { element: <AuthGate />, children: [
    { element: <Layout />, children: [
      { path: "/", element: <Chat /> },
      { path: "/t/:id", element: <Chat /> },
      { path: "/calendar", element: <Calendar />, children: [
        { index: true, element: <CalendarBoard /> },
        { path: "tasks", element: <Routines /> },
        { path: "tasks/:id", element: <Routine /> },
      ] },
      { path: "/routines", element: <Navigate to="/calendar/tasks" replace /> },
      { path: "/routines/:id", element: <RoutineRedirect /> },
      { path: "/memory", element: <Memory /> },
      { path: "/customize", element: <Customize />, children: [
        { index: true, element: <Navigate to="connectors" replace /> },
        { path: "connectors", element: <ConnectorsTab /> },
        { path: "connectors/:name", element: <ConnectorPage /> },
        { path: "plugins", element: <PluginsTab /> },
        { path: "plugins/:name", element: <PluginPage /> },
        { path: "skills", element: <SkillsTab /> },
        { path: "skills/:name", element: <SkillPage /> },
      ] },
    ] },
  ] },
]);
