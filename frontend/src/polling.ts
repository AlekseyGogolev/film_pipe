import { useEffect, useRef } from "react";

interface PollingOptions {
  enabled: boolean;
  intervalMs?: number;
  immediate?: boolean;
  poll: () => Promise<void>;
  onError?: (error: unknown) => void;
}

export function usePolling({
  enabled,
  intervalMs = 1500,
  immediate = true,
  poll,
  onError,
}: PollingOptions) {
  const pollRef = useRef(poll);
  const onErrorRef = useRef(onError);
  const inFlightRef = useRef(false);

  useEffect(() => {
    pollRef.current = poll;
  }, [poll]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    let cancelled = false;

    async function tick() {
      if (cancelled || inFlightRef.current) {
        return;
      }

      inFlightRef.current = true;
      try {
        await pollRef.current();
      } catch (error) {
        if (!cancelled) {
          onErrorRef.current?.(error);
        }
      } finally {
        inFlightRef.current = false;
      }
    }

    if (immediate) {
      void tick();
    }

    const intervalId = window.setInterval(() => {
      void tick();
    }, intervalMs);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [enabled, immediate, intervalMs]);
}
