/** Application root: the router plus the toast host. */

import { RouterProvider } from "react-router";
import { Toaster } from "@/components/ui/sonner";
import { router } from "./routes";

export function App() {
  return (<><RouterProvider router={router} /><Toaster /></>);
}
