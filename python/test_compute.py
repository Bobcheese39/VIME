import numpy as np
import pandas as pd
from time import sleep
from sklearn.datasets import load_iris

def test_compute():
    iris = load_iris()
    df = pd.DataFrame(iris.data, columns=iris.feature_names)
    sleep(np.random.randint(5, 10))
    print("Data Computed")
    return df