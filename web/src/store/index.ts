/** The single zustand store composed of the threads, events, routines, calendar
 *  content, connectors, plugins, drafts, skills, secrets, members and UI slices. */

import { create } from "zustand";
import { createConnectorsSlice, type ConnectorsSlice } from "./connectors";
import { createContentSlice, type ContentSlice } from "./content";
import { createDraftsSlice, type DraftsSlice } from "./drafts";
import { createEventsSlice, type EventsSlice } from "./events";
import { createPluginsSlice, type PluginsSlice } from "./plugins";
import { createMembersSlice, type MembersSlice } from "./members";
import { createModelsSlice, type ModelsSlice } from "./models";
import { createRoutinesSlice, type RoutinesSlice } from "./routines";
import { createSecretsSlice, type SecretsSlice } from "./secrets";
import { createSkillsSlice, type SkillsSlice } from "./skills";
import { createThreadsSlice, type ThreadsSlice } from "./threads";
import { createUiSlice, type UiSlice } from "./ui";

export type Store = ThreadsSlice & EventsSlice & RoutinesSlice & ContentSlice & ConnectorsSlice & PluginsSlice & DraftsSlice & SkillsSlice & SecretsSlice & MembersSlice & ModelsSlice & UiSlice;

export const useStore = create<Store>()((...a) => ({
  ...createThreadsSlice(...a),
  ...createEventsSlice(...a),
  ...createRoutinesSlice(...a),
  ...createContentSlice(...a),
  ...createConnectorsSlice(...a),
  ...createPluginsSlice(...a),
  ...createDraftsSlice(...a),
  ...createSkillsSlice(...a),
  ...createSecretsSlice(...a),
  ...createMembersSlice(...a),
  ...createModelsSlice(...a),
  ...createUiSlice(...a),
}));
