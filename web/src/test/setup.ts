import "@testing-library/jest-dom/vitest";

// jsdom ships no matchMedia; sonner's <Toaster/> and the theme helper both call it.
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener() {}, removeListener() {},
      addEventListener() {}, removeEventListener() {},
      dispatchEvent: () => false,
    }),
  });
}

// jsdom ships no ResizeObserver either; cmdk (the command palette) observes its list.
if (!("ResizeObserver" in globalThis)) {
  Object.defineProperty(globalThis, "ResizeObserver", {
    writable: true,
    value: class { observe() {} unobserve() {} disconnect() {} },
  });
}

// …and no scrollIntoView, which cmdk calls when it selects an item.
Element.prototype.scrollIntoView ??= function () {};

// jsdom implements no Pointer Capture API, and Radix's select opens its list from
// a pointerdown handler that asks whether the trigger has captured the pointer —
// without these it throws before the list is ever mounted.
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= function () {};
Element.prototype.releasePointerCapture ??= function () {};
