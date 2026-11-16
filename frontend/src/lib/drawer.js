// Value-details drawer: one drawer for the whole app, opened from any value.
import { createContext, useContext } from "react";

export const DrawerContext = createContext({ open: () => {}, close: () => {} });

export function useDrawer() {
  return useContext(DrawerContext);
}
