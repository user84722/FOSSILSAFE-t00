# Contributing to FossilSafe

First of all, thank you for your interest in FossilSafe! This project is maintained by a solo developer, and community testing, hardware compatibility reports, and contributions are very valuable.

## How to Contribute

### 1. Hardware Compatibility Reports
LTO tape drives and libraries come in many shapes and sizes. If you have tested FossilSafe with a specific drive or changer, please open an issue or pull request to add it to our compatibility list. Real-world testing is incredibly helpful!

### 2. Bug Reports
If you find a bug, please check the existing issues to see if it has already been reported. If not, open a new issue with a clear title, a description of the problem, and steps to reproduce. Include your hardware details, operating system, and any relevant logs.

### 3. Feature Requests and Discussions
If you have an idea for a new feature or want to discuss the project's direction, feel free to open a discussion or a feature request issue. Keep in mind that as a solo-maintained project, focus is usually on stability and core workflows.

### 4. Pull Requests
Contributions to the codebase and documentation are welcome. 

- Fork the repository and create your branch from `main`.
- Write clear, concise commit messages.
- Ensure your code follows the existing style and conventions in the codebase.
## Developer Certificate of Origin (DCO)

FossilSafe uses the Developer Certificate of Origin (DCO). To certify that you wrote the code or have the right to submit it under the project license, contributors must sign off their commits.

To sign off a commit, use the `-s` (or `--signoff`) flag:

```bash
git commit -s -m "Fix tape drive detection"
```

This will append the required signature to your commit message:

```
Signed-off-by: Jane Doe <jane@example.com>
```

For the full DCO text, please see [DCO.md](DCO.md).

## Code Style Expectations
Prefer clear, readable code over clever tricks. Since this is an infrastructure project, reliability and practical engineering are the top priorities.

## Documentation Improvements
Fixing typos, clarifying instructions, or adding examples in the `docs/` folder is always appreciated. Practical guidance is preferred over marketing language.
