/** A top-level navigation (the OAuth consent page). One function so tests can mock it. */
export const goTo = (url: string) => window.location.assign(url);
