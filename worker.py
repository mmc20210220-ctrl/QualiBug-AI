
# simulated worker service (for future split deployment)

def process(task):
    if task["state"] in ["edge","invalid","race"]:
        return "error"
    return "ok"
