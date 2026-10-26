/** The two questions the app asks in passing — "are you sure?" and "what should
 * it be called?" — as real dialogs.
 *
 * They used to be `window.confirm` / `window.prompt`. Telegram's Mini App
 * webview does not show a native JS dialog, so every one of those buttons was
 * silently dead there: the tap did nothing and nothing said why. These render
 * inside the page, so they work wherever the page does. */

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/i18n";

type ConfirmProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => void;
};

export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel, destructive, onConfirm }: ConfirmProps) {
  const t = useT();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description
            ? <DialogDescription>{description}</DialogDescription>
            : <DialogDescription className="sr-only">{title}</DialogDescription>}
        </DialogHeader>
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button variant={destructive ? "destructive" : "default"}
            onClick={() => { onOpenChange(false); onConfirm(); }}>{confirmLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type PromptProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  label: string;
  initial: string;
  submitLabel: string;
  onSubmit: (value: string) => void;
};

export function PromptDialog({ open, onOpenChange, ...rest }: PromptProps) {
  // The field lives in a child mounted with the dialog, so reopening offers
  // what the row says now rather than what was typed and abandoned last time.
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open && <PromptForm onOpenChange={onOpenChange} {...rest} />}
    </Dialog>
  );
}

function PromptForm({ onOpenChange, title, label, initial, submitLabel, onSubmit }: Omit<PromptProps, "open">) {
  const t = useT();
  const [value, setValue] = useState(initial);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = value.trim();
    if (!v) return;                       // an empty name is not a rename
    onOpenChange(false);
    onSubmit(v);
  };
  return (
    <DialogContent className="sm:max-w-sm">
      <form onSubmit={submit}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="sr-only">{label}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2 py-4">
          <Label htmlFor="ask-dialog-value">{label}</Label>
          <Input id="ask-dialog-value" value={value} autoFocus onChange={(e) => setValue(e.target.value)} />
        </div>
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button type="submit" disabled={!value.trim()}>{submitLabel}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
