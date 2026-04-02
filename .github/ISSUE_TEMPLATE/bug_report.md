---
name: Bug Report
description: Use this template to report a bug.
body:
  - type: markdown
    attributes:
      value: >
        ## Bug Report
        Please check the following items before submitting your report:

        - [ ] I have searched for similar issues

  - type: textarea
    id: description
    attributes:
      label: Description
      description: A clear and concise description of the bug.
      placeholder: "What is the issue?"
      validations:
        required: true

  - type: textarea
    id: steps_to_reproduce
    attributes:
      label: Steps to Reproduce
      description: Steps to reproduce the behavior.
      placeholder: "1. Go to '...'\n2. Click on '...'\n3. Scroll down to '...'\n4. See error"
      validations:
        required: true

  - type: textarea
    id: expected_behavior
    attributes:
      label: Expected Behavior
      description: A clear and concise description of what you expected to happen.
      placeholder: "What were you expecting to happen?"
      validations:
        required: true

  - type: textarea
    id: actual_behavior
    attributes:
      label: Actual Behavior
      description: A clear and concise description of what actually happened.
      placeholder: "What happened instead?"
      validations:
        required: true

  - type: textarea
    id: system_info
    attributes:
      label: System Info
      description: Please provide your system information (OS, version, etc.).
      placeholder: "What is your OS and version?"
      validations:
        required: false

  - type: textarea
    id: logs
    attributes:
      label: Logs
      description: Any logs that might be helpful in diagnosing the issue.
      placeholder: "Paste any logs here"
      validations:
        required: false
...