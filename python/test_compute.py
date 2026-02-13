import numpy as np
import pandas as pd
from time import sleep
def test_compute():
    x = np.random.randint(0, 100)
    y = np.random.randint(0, 100)
    z = x + y
    df = pd.DataFrame({"x": x, "y": y, "z": z}, index='x')
    sleep(np.random.randint(5, 10))
    print("Data Computed")
    return df