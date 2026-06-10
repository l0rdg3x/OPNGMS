import { Button, Group, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useT } from "../i18n";
import { ConfirmModal } from "./ConfirmModal";

export function DeviceActions({ tenantId, deviceId }: { tenantId: string; deviceId: string }) {
  const t = useT();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [confirmOpen, setConfirmOpen] = useState(false);
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
      qc.invalidateQueries({ queryKey: ["devices", tenantId] }); // also refresh the list
      notifications.show({ message: `${t.deviceActions.testNotification}: ${d.status}` });
    },
    onError: () => notifications.show({ color: "red", message: t.deviceActions.testFailed }),
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
    onError: () => notifications.show({ color: "red", message: t.deviceActions.deleteFailed }),
  });

  return (
    <>
      <Group mt="md">
        <Button onClick={() => test.mutate()} loading={test.isPending}>
          {t.deviceActions.testConnection}
        </Button>
        <Button
          color="red"
          variant="light"
          onClick={() => setConfirmOpen(true)}
          data-testid="btn-delete"
        >
          {t.deviceActions.delete}
        </Button>
      </Group>

      <ConfirmModal
        opened={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => {
          setConfirmOpen(false);
          remove.mutate();
        }}
        title={t.confirm.deleteDevice}
        body={t.confirm.deleteDeviceBody}
        loading={remove.isPending}
      />

      {test.data && (
        <Text data-testid="test-result">
          {t.deviceActions.testResult}: {test.data.status}
        </Text>
      )}
    </>
  );
}
