import { Alert, Loader, Stack, Text } from "@mantine/core";
import { useT } from "../i18n";
import { CapabilitiesPanel } from "./CapabilitiesPanel";
import { ConfigTree } from "./ConfigTree";
import { useConfigCapabilities, useConfigModel } from "./hooks";

export function ConfigTab({ deviceId }: { deviceId: string }) {
  const t = useT();
  const model = useConfigModel(deviceId);
  const caps = useConfigCapabilities(deviceId);

  if (model.isLoading || caps.isLoading) return <Loader />;
  if (model.error || caps.error)
    return <Alert color="red">{t.config.noConfigYet}</Alert>;
  // A 404 (no snapshot yet) resolves to null (not an error) -> empty state.
  if (model.data === null) return <Text c="dimmed">{t.config.noConfigYet}</Text>;

  return (
    <Stack>
      {caps.data && <CapabilitiesPanel inv={caps.data} />}
      {model.data && <ConfigTree root={model.data} />}
    </Stack>
  );
}
