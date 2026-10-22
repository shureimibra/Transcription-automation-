// THE NAME OUTSIDE EAGLE TASKERS SEE, IN ONE PLACE.
//
// Taskers never see "Shurz": not in the page title, the header, or the web
// address. Neither value below is final. Renaming the product, or moving
// its pages, is a change to this file and nothing else.

/** Shown in the header, the sign-in page and the browser tab. */
export const PRODUCT_NAME = 'Workdesk';

/** Every tasker page lives under this path. */
export const TASKER_BASE = '/desk';

export const taskerPath = {
  login: `${TASKER_BASE}/login`,
  signup: `${TASKER_BASE}/signup`,
  search: TASKER_BASE,
  task: (id) => `${TASKER_BASE}/task/${id}`,
};
