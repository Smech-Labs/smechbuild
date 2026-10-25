"""Run the build in a rootless podman container with no network."""

import os
import subprocess


class SandboxError(Exception):
    pass


def run(cfg, recipe, dirs, env, log_path, container_name):
    """dirs: src, obj, destdir, sysroot. Returns on success, raises on failure.
    The whole build output goes to log_path."""
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.sh")
    cmd = [
        "podman", "run", "--rm", "--name", container_name,
        "--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        # label=disable: do not relabel host trees (toolchain, cache) for SELinux;
        # isolation comes from the namespaces, the dropped caps and no network.
        "--security-opt=label=disable",
        f"--cpus={cfg.jobs}", f"--timeout={cfg.build_timeout}",
        "--entrypoint", "/bin/sh",
        "-v", f"{dirs['src']}:/build/src:rw",
        "-v", f"{dirs['obj']}:/build/obj:rw",
        "-v", f"{dirs['destdir']}:/destdir:rw",
        "-v", f"{dirs['sysroot']}:/sysroot:ro",
        "-v", f"{recipe.path}:/recipe:ro",
        "-v", f"{runner}:/runner.sh:ro",
    ]
    if "extra" in dirs:
        cmd += ["-v", f"{dirs['extra']}:/build/extra:ro"]
    if cfg.toolchain:
        cmd += ["-v", f"{cfg.toolchain}:/toolchain:ro"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [cfg.image, "/runner.sh"]
    with open(log_path, "wb") as log:
        log.write((" ".join(cmd) + "\n").encode())
        log.flush()
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise SandboxError(f"build exited with status {r.returncode} (see {log_path})")
