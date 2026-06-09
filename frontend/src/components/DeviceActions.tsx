import { Button, Group, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

export function DeviceActions({ tenantId, deviceId }: { tenantId: string; deviceId: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const pathParams = { params: { path: { tenant_id: tenantId, device_id: deviceId } } } as const;

  const test = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/test-connection",
        pathParams,
      );
      if (error || !data) throw new Error("test failed");
      return data; // narrowed: non-undefined
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["device", tenantId, deviceId] });
      qc.invalidateQueries({ queryKey: ["devices", tenantId] }); // Fix 3: aggiorna anche la lista
      notifications.show({ message: `Test: ${d.status}` }); // no cast needed now
    },
    onError: () => notifications.show({ color: "red", message: "Test connessione fallito" }), // Fix 2
  });

  const remove = useMutation({
    mutationFn: async () => {
      const { error } = await api.DELETE(
        "/api/tenants/{tenant_id}/devices/{device_id}",
        pathParams,
      );
      if (error) throw new Error("delete failed");
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices", tenantId] });
      navigate("/devices");
    },
    onError: () => notifications.show({ color: "red", message: "Eliminazione fallita" }),
  });

  return (
    <>
      <Group mt="md">
        <Button onClick={() => test.mutate()} loading={test.isPending}>Testa connessione</Button>
        <Button color="red" variant="light" onClick={() => remove.mutate()}>Elimina</Button>
      </Group>
      {test.data && (
        <Text data-testid="test-result">
          Risultato test: {test.data.status}
        </Text>
      )}
    </>
  );
}
