/** Composer attachment adapter: every added file is uploaded to the thread's inbox. */

import type { AttachmentAdapter, PendingAttachment } from "@assistant-ui/react";
import { api } from "@/api/client";

/** `AttachmentAdapter.add` is declared as promise-or-generator; ours always resolves. */
export type UploadAttachmentAdapter = Omit<AttachmentAdapter, "add"> & {
  add(state: { file: File }): Promise<PendingAttachment>;
};

export function attachmentAdapter(threadId: string): UploadAttachmentAdapter {
  return {
    // Any file: the server stores uploads as-is (.eml, .msg, archives, …) and the agent reads them from the inbox.
    accept: "*",
    async add({ file }) {
      const rec = await api.uploads.put(threadId, file);
      return {
        id: rec.file_id,
        type: rec.media_type.startsWith("image/") ? "image" : "document",
        name: rec.name,
        contentType: rec.media_type,
        file,
        status: { type: "requires-action", reason: "composer-send" },
      };
    },
    async send(attachment) {
      return { ...attachment, status: { type: "complete" }, content: [] };
    },
    async remove() {
      /* the inbox file is harmless; nothing to undo */
    },
  };
}
