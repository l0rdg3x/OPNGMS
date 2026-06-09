// Mirrors the backend config_model node (GET /config/model is response_model=dict).
export interface ConfigNode {
  tag: string;
  path: string;
  attributes: Record<string, string | null>;
  children: ConfigNode[];
  value: string | null;
  sensitive: boolean;
}
