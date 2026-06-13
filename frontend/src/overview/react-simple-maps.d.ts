// Minimal local type shim for `react-simple-maps@3`, which ships no bundled types and
// has no `@types/react-simple-maps`. It renders correctly under React 19 (verified), so we
// only need enough surface to type the components we use here. Kept intentionally small —
// extend it if we start using more of the library.
declare module "react-simple-maps" {
  import type {
    ComponentType,
    CSSProperties,
    MouseEvent as ReactMouseEvent,
    ReactNode,
    SVGProps,
  } from "react";

  /** A single TopoJSON-derived feature handed to the `Geographies` render prop. */
  export interface RSMGeography {
    rsmKey: string;
    /** ISO numeric country id from the source topojson (e.g. "643" for Russia). */
    id: string;
    properties: Record<string, unknown>;
    [key: string]: unknown;
  }

  export interface ComposableMapProps {
    width?: number;
    height?: number;
    projection?: string;
    projectionConfig?: Record<string, unknown>;
    className?: string;
    style?: SVGProps<SVGSVGElement>["style"];
    children?: ReactNode;
  }
  export const ComposableMap: ComponentType<ComposableMapProps>;

  export interface ZoomableGroupProps {
    center?: [number, number];
    zoom?: number;
    minZoom?: number;
    maxZoom?: number;
    children?: ReactNode;
  }
  export const ZoomableGroup: ComponentType<ZoomableGroupProps>;

  export interface GeographiesProps {
    geography: unknown;
    children: (args: { geographies: RSMGeography[] }) => ReactNode;
  }
  export const Geographies: ComponentType<GeographiesProps>;

  /** Per-interaction style map react-simple-maps merges onto the rendered `<path>`. */
  export interface GeographyStyle {
    default?: CSSProperties;
    hover?: CSSProperties;
    pressed?: CSSProperties;
  }
  export interface GeographyProps
    extends Omit<SVGProps<SVGPathElement>, "fill" | "stroke" | "style"> {
    geography: RSMGeography;
    fill?: string;
    stroke?: string;
    strokeWidth?: number;
    style?: GeographyStyle;
    onMouseEnter?: (event: ReactMouseEvent<SVGPathElement>) => void;
    onMouseMove?: (event: ReactMouseEvent<SVGPathElement>) => void;
    onMouseLeave?: (event: ReactMouseEvent<SVGPathElement>) => void;
  }
  export const Geography: ComponentType<GeographyProps>;
}
