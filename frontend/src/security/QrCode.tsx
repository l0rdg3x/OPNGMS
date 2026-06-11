import { useEffect, useState } from "react";
import QRCode from "qrcode";

interface QrCodeProps {
  /** The data to encode (here: an otpauth:// provisioning URI). */
  value: string;
  /** Rendered pixel size (width = height). */
  size?: number;
}

/**
 * Renders a QR code for `value` as an `<img>` backed by a PNG data-URI (via the `qrcode` library —
 * a small, reliable encoder). Using a rasterized data-URI (not injected SVG/HTML) keeps this free of
 * any markup-injection surface. While it generates (or if generation fails) nothing is shown — the
 * caller always also exposes the secret/URI as selectable text, so a missing QR is never a dead end.
 */
export function QrCode({ value, size = 200 }: QrCodeProps) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    QRCode.toDataURL(value, {
      margin: 1,
      width: size,
      errorCorrectionLevel: "M",
      color: { dark: "#0b1220ff", light: "#ffffffff" },
    })
      .then((out) => {
        if (!cancelled) setSrc(out);
      })
      .catch(() => {
        if (!cancelled) setSrc(null);
      });
    return () => {
      cancelled = true;
    };
  }, [value, size]);

  if (!src) return null;

  return (
    <img
      src={src}
      width={size}
      height={size}
      alt="MFA QR code"
      data-testid="mfa-qr"
      style={{ background: "#ffffff", padding: 8, borderRadius: 8 }}
    />
  );
}
