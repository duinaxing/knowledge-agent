import os


def main():
    os.environ['VALIDATION_SUITE']='quality'
    from knowledge_loadtest.launch import main as launch
    launch()


if __name__=='__main__':main()
