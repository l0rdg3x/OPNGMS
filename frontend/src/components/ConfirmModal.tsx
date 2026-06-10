import { Button, Group, Modal, Text } from "@mantine/core";
import { useT } from "../i18n";

interface ConfirmModalProps {
  opened: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title?: string;
  body?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  loading?: boolean;
}

/**
 * Generic confirmation modal for destructive actions.
 * All strings default to the i18n `confirm.*` keys; callers may override.
 */
export function ConfirmModal({
  opened,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel,
  cancelLabel,
  loading = false,
}: ConfirmModalProps) {
  const t = useT();
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={title ?? t.confirm.title}
      data-testid="confirm-modal"
      transitionProps={{ duration: 0 }}
    >
      {body && <Text mb="md">{body}</Text>}
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose} data-testid="confirm-cancel">
          {cancelLabel ?? t.confirm.cancel}
        </Button>
        <Button
          color="red"
          onClick={onConfirm}
          loading={loading}
          data-testid="confirm-ok"
        >
          {confirmLabel ?? t.confirm.confirm}
        </Button>
      </Group>
    </Modal>
  );
}
