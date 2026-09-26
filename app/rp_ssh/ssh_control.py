from __future__ import annotations

import time
from typing import Optional

import paramiko


class RedPitayaSSHError(RuntimeError):
    """Raised when an SSH connection to the Red Pitaya, or one of the
    remote commands it runs, fails."""


class RedPitayaSSH:
    """Thin wrapper around an SSH session to a Red Pitaya, used to switch
    the board between the Lock-In Amplifier and Oscilloscope FPGA images.

    Usage::

        with RedPitayaSSH(host, password) as ssh:
            ssh.switch_to_lia()
    """

    def __init__(
        self,
        host: str,
        password: str,
        username: str = "root",
        timeout: float = 10.0,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout
        self._client: Optional["paramiko.SSHClient"] = None

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        except Exception as exc:
            raise RedPitayaSSHError(
                f"Could not connect to {self.host}: {exc}"
            ) from exc
        self._client = client

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    def __enter__(self) -> "RedPitayaSSH":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    # ------------------------------------------------------------------ #
    # Low level command execution
    # ------------------------------------------------------------------ #
    # paramiko's exec_command() gets a non-interactive, non-login shell,
    # which typically does NOT source /etc/profile or ~/.bashrc. On Red
    # Pitaya OS, tools like fpgautil and monitor live under
    # /opt/redpitaya/bin, which only ends up on PATH for interactive/login
    # sessions -- so a plain SSH command can fail with "command not
    # found" even though the same command works fine when typed at an
    # interactive prompt. Prepending PATH here fixes it regardless of the
    # remote login shell (sh/dash/bash all support `VAR=val command`).
    #
    # IMPORTANT: the VAR=val prefix only works as an env-scoped
    # assignment when it is the first word of the command. _env_prefix()
    # must therefore go *before* anything else on the line (including
    # nohup) -- putting it after nohup, as an argument to nohup, makes
    # the shell pass it along as nohup's literal program name instead,
    # which fails silently.
    _EXTRA_PATH = "/opt/redpitaya/bin:/opt/redpitaya/sbin:/usr/local/bin"

    def _env_prefix(self) -> str:
        return f'PATH="$PATH:{self._EXTRA_PATH}"'

    def _wrap(self, command: str) -> str:
        return f"{self._env_prefix()} {command}"

    def run(self, command: str, check: bool = True) -> tuple[int, str, str]:
        """Run a foreground command on the RP and wait for it to exit."""
        if self._client is None:
            raise RedPitayaSSHError("Not connected")

        _stdin, stdout, stderr = self._client.exec_command(
            self._wrap(command),
            timeout=self.timeout,
        )
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")

        if check and exit_status != 0:
            raise RedPitayaSSHError(
                f"Command failed ({exit_status}): {command}\n{err.strip()}"
            )

        return exit_status, out, err

    def run_background(self, command: str) -> None:
        """Launch a long-running command detached from this SSH session,
        so it keeps running once the session (and this connection) closes.
        """
        if self._client is None:
            raise RedPitayaSSHError("Not connected")

        detached = (
            f"{self._env_prefix()} nohup {command} "
            "> /dev/null 2>&1 < /dev/null & disown"
        )
        # Fire-and-forget: the remote shell forks the job and returns
        # immediately. We don't wait for (or expect) an exit status here,
        # since the whole point is that it keeps running after we leave.
        self._client.exec_command(detached, timeout=self.timeout)

    # ------------------------------------------------------------------ #
    # High level operations
    # ------------------------------------------------------------------ #
    def switch_to_lia(self) -> None:
        """Stop the stock Red Pitaya services, load the LIA bitstream, and
        start the LIA register server."""
        self.run("systemctl stop redpitaya_nginx", check=False)
        self.run("systemctl stop redpitaya_scpi", check=False)

        # A previous run may have left a monitor instance behind (e.g. the
        # app was closed without cleanly switching back to Oscilloscope
        # first). run_background() below is fire-and-forget, so if we
        # don't clear out any stale instance first, the new one can fail
        # to bind its port and die immediately without us knowing --
        # which is exactly what forces a manual restart.
        self.run("pkill -f lia_server_monitor.py", check=False)

        self.run("fpgautil -b /root/rp_lia_top.bit.bin")
        self.run_background("python3 /root/lia_server_monitor.py")

        # Confirm it's actually alive rather than trusting the
        # fire-and-forget launch blindly.
        time.sleep(1.0)
        exit_status, out, err = self.run(
            "pgrep -f lia_server_monitor.py",
            check=False,
        )
        if exit_status != 0:
            # exit_status 1 from pgrep means "no matching process" (the
            # actual failure this check is for); anything else (e.g. 127)
            # usually means pgrep itself isn't available on this image --
            # surface the raw output either way rather than guessing.
            detail = (err or out).strip()
            raise RedPitayaSSHError(
                "lia_server_monitor.py did not start (pgrep exit "
                f"{exit_status} ~1s after launch)"
                + (f": {detail}" if detail else "")
            )
            raise RedPitayaSSHError(
                "lia_server_monitor.py did not start (no matching "
                "process found ~1s after launch)"
            )

    def switch_to_oscilloscope(self) -> None:
        """Stop the LIA server, reload the stock FPGA image, and restart
        the stock Red Pitaya services."""
        self.run("pkill -f lia_server_monitor.py", check=False)
        self.run(
            "fpgautil -b "
            "/opt/redpitaya/fpga/$(monitor -f)/v0.94/fpga.bit.bin"
        )
        self.run("systemctl start redpitaya_nginx", check=False)
        self.run("systemctl start redpitaya_scpi", check=False)