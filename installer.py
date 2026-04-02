def create_venv(env_name):
    import os
    import subprocess
    import sys

    # Validate the input
    if not env_name:
        raise ValueError("Environment name must not be empty.")

    # Check if the virtual environment already exists
    if os.path.exists(env_name):
        print(f"Virtual environment '{env_name}' already exists.")
        return

    try:
        # Create the virtual environment
        subprocess.check_call([sys.executable, '-m', 'venv', env_name])
        print(f"Virtual environment '{env_name}' created successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to create virtual environment: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")
