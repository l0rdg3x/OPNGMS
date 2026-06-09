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
      const { data } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/test-connection",
        pathParams,
      );
      return data;
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["device", tenantId, deviceId] });
      notifications.show({ message: `Test: ${(d as { status: string }).status}` });
    },
  });

  const remove = useMutation({
    mutationFn: async () => {
      await api.DELETE("/api/tenants/{tenant_id}/devices/{device_id}", pathParams);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices", tenantId] });
      navigate("/");
    },
  });

  return (
    <>
      <Group mt="md">
        <Button onClick={() => test.mutate()} loading={test.isPending}>Testa connessione</Button>
        <Button color="red" variant="light" onClick={() => remove.mutate()}>Elimina</Button>
      </Group>
      {test.data && (
        <Text data-testid="test-result">
          Risultato test: {(test.data as { status: string }).status}
        </Text>
      )}
    </>
  );
}
