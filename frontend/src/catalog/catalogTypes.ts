// frontend/src/catalog/catalogTypes.ts
export type CatalogField = {
  path: string;
  type: "bool" | "int" | "string" | "enum" | "multienum" | "network" | "ref" | "raw";
  options?: string[];
  label?: string;
  required?: boolean;
};

export type CatalogGrid = {
  path: string;
  endpoints: Record<string, string>;
  fields: CatalogField[];
};

export type CatalogModel = {
  id: string;
  title: string;
  model_root: string;
  endpoints: Record<string, string>;
  fields: CatalogField[];
  grids: CatalogGrid[];
  pages: { id: string; fields: string[] }[];
  read_only?: boolean;
};

export type GridRow = { uuid: string } & Record<string, string | string[]>;

export type CatalogModelLive = {
  model: CatalogModel;
  values: Record<string, string | string[]>;
  grids: Record<string, GridRow[]>;
  field_options: Record<string, { value: string; label: string }[]>;
  grid_field_options: Record<string, Record<string, { value: string; label: string }[]>>;
  reachable: boolean;
  read_only: boolean;
};

export type CatalogGridOp =
  | { op: "add"; grid: string; item: Record<string, string> }
  | { op: "set"; grid: string; uuid: string; item: Record<string, string> }
  | { op: "del"; grid: string; uuid: string };

export type CatalogChangeBody = {
  model_id: string;
  scalars: Record<string, string>;
  grids: CatalogGridOp[];
};

export type MenuNode = {
  id: string;
  label: string;
  order: number;
  icon?: string;
  url?: string;
  model_id?: string | null;
  children?: MenuNode[];
};

// Per-model field-level diff between the device's catalog version and a baseline.
export type CatalogModelDiff = {
  added_fields: string[];
  removed_fields: string[];
  changed_fields: string[];
};

// The `diff` payload of the /catalog/diff endpoint (the cross-version delta itself).
export type CatalogDiffData = {
  added_models: string[];
  removed_models: string[];
  models: Record<string, CatalogModelDiff>;
};

// Full /catalog/diff response: the device version (`to`) vs a baseline (`from`).
export type CatalogDiff = {
  from: string | null;
  to: string;
  available_baselines: string[];
  diff: CatalogDiffData;
};
